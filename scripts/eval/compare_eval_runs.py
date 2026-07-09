#!/usr/bin/env python3
"""Compare one or more deterministic eval run directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import load_jsonl


REQUIRED_METRIC_FIELDS = (
    "matched_rows",
    "mean_score",
    "median_score",
    "min_score",
    "max_score",
    "safety_failures",
    "error_counts",
    "score_by_task_type",
)


def _load_metrics(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not path.exists():
        return None, [f"metrics file not found: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"malformed JSON in {path}: {exc.msg}"]
    except (OSError, UnicodeError) as exc:
        return None, [f"could not read metrics file {path}: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"metrics file must be a JSON object: {path}"]
    for field in REQUIRED_METRIC_FIELDS:
        if field not in payload:
            errors.append(f"metrics file missing {field}: {path}")
    return payload, errors


def _load_row_results(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    loaded, problems = load_jsonl(path)
    errors = [problem.message for problem in problems]
    rows: list[dict[str, Any]] = []
    for line, row in loaded:
        row_id = row.get("id")
        score = row.get("score")
        if not isinstance(row_id, str) or not row_id:
            errors.append(f"{path}:{line}: row result id must be a non-empty string")
            continue
        if not isinstance(score, (int, float)):
            errors.append(f"{path}:{line}: row result score must be numeric")
            continue
        rows.append(row)
    return rows, errors


def _weakest_rows(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    weakest = sorted(rows, key=lambda row: (float(row.get("score", 0.0)), str(row.get("id"))))[:limit]
    return [
        {
            "id": row["id"],
            "score": float(row["score"]),
            "error_count": len(row.get("errors", [])) if isinstance(row.get("errors"), list) else 0,
            "safety_failure_count": len(row.get("safety_failures", [])) if isinstance(row.get("safety_failures"), list) else 0,
        }
        for row in weakest
    ]


def _run_name(run_dir: Path) -> str:
    return run_dir.name or str(run_dir)


def _pairwise_overlap(run_rows: dict[str, set[str]]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for name, ids in run_rows.items():
        matrix[name] = {}
        for other_name, other_ids in run_rows.items():
            matrix[name][other_name] = len(ids & other_ids)
    return matrix


def _largest_score_differences(run_details: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    by_row: dict[str, dict[str, float]] = {}
    run_names = [detail["name"] for detail in run_details]
    for detail in run_details:
        name = detail["name"]
        for row in detail["row_results"]:
            by_row.setdefault(row["id"], {})[name] = float(row["score"])
    differences: list[dict[str, Any]] = []
    for row_id, scores_by_run in by_row.items():
        if len(scores_by_run) < 2:
            continue
        scores = list(scores_by_run.values())
        differences.append({
            "id": row_id,
            "score_spread": round(max(scores) - min(scores), 6),
            "scores_by_run": dict(sorted(scores_by_run.items())),
            "missing_runs": [name for name in run_names if name not in scores_by_run],
        })
    differences.sort(key=lambda item: (-item["score_spread"], item["id"]))
    return differences[:limit]


def _comparison_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Eval run comparison",
        "",
        "## Run summary",
        "",
        "| Run | Matched | Mean | Median | Min | Max | Safety failures |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run in summary["runs"]:
        lines.append(
            f"| `{run['name']}` | {run['matched_rows']} | {run['mean_score']:.3f} | "
            f"{run['median_score']:.3f} | {run['min_score']:.3f} | {run['max_score']:.3f} | {run['safety_failures']} |"
        )
    lines.extend([
        "",
        "## Overlap",
        "",
        "| Run | " + " | ".join(f"`{name}`" for name in summary["run_names"]) + " | Missing from union |",
        "|---|" + "---:|" * (len(summary["run_names"]) + 1),
    ])
    for name in summary["run_names"]:
        overlaps = " | ".join(str(summary["pairwise_overlap"][name][other]) for other in summary["run_names"])
        lines.append(f"| `{name}` | {overlaps} | {len(summary['missing_by_run'][name])} |")
    lines.extend(["", "## Largest score differences", ""])
    if summary["largest_score_differences"]:
        lines.append("| Row | Spread | Scores | Missing runs |")
        lines.append("|---|---:|---|---|")
        for item in summary["largest_score_differences"]:
            score_text = ", ".join(f"{name}={score:.3f}" for name, score in item["scores_by_run"].items())
            missing = ", ".join(item["missing_runs"]) if item["missing_runs"] else "none"
            lines.append(f"| `{item['id']}` | {item['score_spread']:.3f} | {score_text} | {missing} |")
    else:
        lines.append("No overlapping rows had scores from more than one run.")
    for run in summary["runs"]:
        lines.extend(["", f"## Weakest rows: `{run['name']}`", ""])
        lines.append("| Row | Score | Errors | Safety failures |")
        lines.append("|---|---:|---:|---:|")
        for item in run["weakest_rows"]:
            lines.append(
                f"| `{item['id']}` | {item['score']:.3f} | {item['error_count']} | {item['safety_failure_count']} |"
            )
        lines.append("")
        lines.append(f"- Error counts: `{json.dumps(run['error_counts'], sort_keys=True)}`")
        lines.append(f"- Score by task type: `{json.dumps(run['score_by_task_type'], sort_keys=True)}`")
    return "\n".join(lines).rstrip() + "\n"


def compare_eval_runs(
    run_dirs: list[Path],
    output_md: Path,
    output_json: Path,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    if len(run_dirs) < 2:
        summary = {"ok": False, "errors": ["at least two --runs directories are required"], "warnings": []}
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("# Eval run comparison\n\nComparison failed.\n", encoding="utf-8")
        return summary, 1

    run_details: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for run_dir in run_dirs:
        name = _run_name(run_dir)
        if name in seen_names:
            errors.append(f"duplicate run name from directory names: {name}")
            continue
        seen_names.add(name)
        metrics, metric_errors = _load_metrics(run_dir / "metrics.json")
        row_results, row_errors = _load_row_results(run_dir / "row_results.jsonl")
        errors.extend(metric_errors)
        errors.extend(row_errors)
        if metrics is None:
            continue
        scores = [float(row["score"]) for row in row_results]
        matched_rows = int(metrics["matched_rows"]) if isinstance(metrics.get("matched_rows"), int) else len(row_results)
        if matched_rows != len(row_results):
            warnings.append(
                f"run {name} metrics.json matched_rows={matched_rows} but row_results.jsonl has {len(row_results)} rows"
            )
        run_details.append({
            "name": name,
            "path": str(run_dir),
            "matched_rows": matched_rows,
            "mean_score": float(metrics["mean_score"]),
            "median_score": float(metrics["median_score"]) if row_results else 0.0,
            "min_score": float(metrics["min_score"]) if row_results else 0.0,
            "max_score": float(metrics["max_score"]) if row_results else 0.0,
            "safety_failures": int(metrics["safety_failures"]),
            "error_counts": metrics["error_counts"] if isinstance(metrics.get("error_counts"), dict) else {},
            "score_by_task_type": metrics["score_by_task_type"] if isinstance(metrics.get("score_by_task_type"), dict) else {},
            "weakest_rows": _weakest_rows(row_results),
            "row_results": row_results,
            "row_ids": {row["id"] for row in row_results},
            "score_mean_recomputed": round(statistics.mean(scores), 6) if scores else 0.0,
        })
    if errors:
        summary = {"ok": False, "errors": errors, "warnings": warnings}
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("# Eval run comparison\n\nComparison failed.\n", encoding="utf-8")
        return summary, 1

    run_names = [detail["name"] for detail in run_details]
    union_ids = set().union(*(detail["row_ids"] for detail in run_details))
    shared_ids = set.intersection(*(detail["row_ids"] for detail in run_details)) if run_details else set()
    missing_by_run = {
        detail["name"]: sorted(union_ids - detail["row_ids"])
        for detail in run_details
    }
    pairwise_overlap = _pairwise_overlap({detail["name"]: detail["row_ids"] for detail in run_details})
    summary = {
        "ok": True,
        "run_names": run_names,
        "runs": [
            {
                key: value
                for key, value in detail.items()
                if key not in {"row_results", "row_ids"}
            }
            for detail in run_details
        ],
        "union_rows": len(union_ids),
        "shared_rows_all_runs": len(shared_ids),
        "pairwise_overlap": pairwise_overlap,
        "missing_by_run": missing_by_run,
        "largest_score_differences": _largest_score_differences(run_details),
        "errors": [],
        "warnings": warnings,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(_comparison_markdown(summary), encoding="utf-8")
    return summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True, nargs="+", type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = compare_eval_runs(args.runs, args.output_md, args.output_json)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Eval run comparison completed." if result["ok"] else "Eval run comparison failed.")
        if result["errors"]:
            print("Errors:")
            for item in result["errors"]:
                print(f"- {item}")
        if result["warnings"]:
            print("Warnings:")
            for item in result["warnings"]:
                print(f"- {item}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
