#!/usr/bin/env python3
"""Compare baseline and fine-tuned teacher-distill RTL evaluation score reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


COMPARE_CATEGORY_KEYS = (
    "overall_valid",
    "json_valid",
    "schema_valid",
    "claim_safety_valid",
    "source_id_match",
    "reference_only_behavior_valid",
    "candidate_bug_behavior_valid",
)


def _load_summary(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not path.exists():
        return None, [f"score file not found: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"malformed JSON in {path}: {exc.msg}"]
    if not isinstance(payload, dict):
        return None, [f"score file must be a JSON object: {path}"]
    for field in ("category_counts", "row_results", "failures_by_source_id"):
        if field not in payload:
            errors.append(f"score file missing {field}: {path}")
    return payload, errors


def _rate(summary: dict[str, Any], key: str) -> float | None:
    counts = summary["category_counts"].get(key, {})
    return counts.get("rate")


def _row_map(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["source_id"]): row for row in summary.get("row_results", [])}


def _compare_category(
    baseline_rows: dict[str, dict[str, Any]],
    finetuned_rows: dict[str, dict[str, Any]],
    key: str,
) -> tuple[list[str], list[str]]:
    improvements: list[str] = []
    regressions: list[str] = []
    for source_id in sorted(set(baseline_rows) & set(finetuned_rows)):
        baseline_value = baseline_rows[source_id]["categories"].get(key)
        finetuned_value = finetuned_rows[source_id]["categories"].get(key)
        if baseline_value is None or finetuned_value is None:
            continue
        if baseline_value is False and finetuned_value is True:
            improvements.append(source_id)
        elif baseline_value is True and finetuned_value is False:
            regressions.append(source_id)
    return improvements, regressions


def _comparison_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# RTL eval comparison",
        "",
        "## Rate comparison",
        "",
        "| Category | Baseline | Fine-tuned | Delta |",
        "|---|---:|---:|---:|",
    ]
    for key, item in summary["metric_deltas"].items():
        baseline = "n/a" if item["baseline"] is None else f"{item['baseline']:.3f}"
        finetuned = "n/a" if item["finetuned"] is None else f"{item['finetuned']:.3f}"
        delta = "n/a" if item["delta"] is None else f"{item['delta']:+.3f}"
        lines.append(f"| `{key}` | {baseline} | {finetuned} | {delta} |")
    lines.extend(["", "## Improvements", ""])
    for key, values in summary["improvements"].items():
        lines.append(f"- `{key}`: {', '.join(values) if values else 'none'}")
    lines.extend(["", "## Regressions", ""])
    for key, values in summary["regressions"].items():
        lines.append(f"- `{key}`: {', '.join(values) if values else 'none'}")
    lines.extend(["", "## Failure coverage", ""])
    lines.append(f"- Baseline failure rows: {len(summary['baseline_failures_by_source_id'])}")
    lines.append(f"- Fine-tuned failure rows: {len(summary['finetuned_failures_by_source_id'])}")
    return "\n".join(lines) + "\n"


def compare_rtl_eval_runs(
    baseline_path: Path,
    finetuned_path: Path,
    output_md: Path,
    output_json: Path,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    baseline, baseline_errors = _load_summary(baseline_path)
    finetuned, finetuned_errors = _load_summary(finetuned_path)
    errors.extend(baseline_errors)
    errors.extend(finetuned_errors)
    if errors or baseline is None or finetuned is None:
        summary = {
            "ok": False,
            "errors": errors,
            "warnings": [],
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("# RTL eval comparison\n\nComparison failed.\n", encoding="utf-8")
        return summary, 1

    baseline_rows = _row_map(baseline)
    finetuned_rows = _row_map(finetuned)
    metric_deltas: dict[str, dict[str, float | None]] = {}
    improvements: dict[str, list[str]] = {}
    regressions: dict[str, list[str]] = {}
    for key in COMPARE_CATEGORY_KEYS:
        baseline_rate = _rate(baseline, key)
        finetuned_rate = _rate(finetuned, key)
        delta = None if baseline_rate is None or finetuned_rate is None else round(finetuned_rate - baseline_rate, 6)
        metric_deltas[key] = {
            "baseline": baseline_rate,
            "finetuned": finetuned_rate,
            "delta": delta,
        }
        improved, regressed = _compare_category(baseline_rows, finetuned_rows, key)
        improvements[key] = improved
        regressions[key] = regressed

    summary = {
        "ok": True,
        "baseline": str(baseline_path),
        "finetuned": str(finetuned_path),
        "metric_deltas": metric_deltas,
        "improvements": improvements,
        "regressions": regressions,
        "baseline_failures_by_source_id": baseline.get("failures_by_source_id", {}),
        "finetuned_failures_by_source_id": finetuned.get("failures_by_source_id", {}),
        "errors": [],
        "warnings": [],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(_comparison_markdown(summary), encoding="utf-8")
    return summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--finetuned", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = compare_rtl_eval_runs(
        baseline_path=args.baseline,
        finetuned_path=args.finetuned,
        output_md=args.output_md,
        output_json=args.output_json,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("RTL eval comparison completed." if result["ok"] else "RTL eval comparison failed.")
        if result["errors"]:
            print("Errors:")
            for item in result["errors"]:
                print(f"- {item}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
