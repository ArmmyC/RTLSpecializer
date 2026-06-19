#!/usr/bin/env python3
"""Finalize a ready, manually reviewed batch through the local deterministic pipeline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.release import ReleaseConfig, build_release
from scripts.dataset.review_promotion import PromotionConfig, load_rows, promote_rows, rejected_path_for
from scripts.dataset.review_readiness import check_review_readiness, load_review_files, write_readiness_reports
from scripts.dataset.validation import validate_dataset_file
from scripts.eval.evaluator import evaluate_dataset, load_candidate_answers, load_dataset_rows
from scripts.eval.make_baseline_candidates import make_candidates


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class FinalizationConfig:
    batch_dir: Path
    processed_output: Path
    promotion_report: Path
    release_name: str
    release_output_dir: Path
    candidate_output: Path
    eval_output_dir: Path
    golden_input: Path = Path("data/golden/golden_v0.1.jsonl")
    seed: int = 7
    allow_source_overlap: bool = False
    force: bool = False


def _inside_local_data(path: Path) -> bool:
    return any(
        part.lower() == ".local_data"
        for candidate in (path.absolute(), path.resolve())
        for part in candidate.parts
    )


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _exists_or_symlink(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _is_filesystem_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved.parent == resolved


def _symlink_in_ancestry(path: Path) -> Path | None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _paths(config: FinalizationConfig) -> dict[str, Path]:
    return {
        "selected": config.batch_dir / "selected_rows.jsonl",
        "reviewed": config.batch_dir / "reviewed_rows.jsonl",
        "readiness_json": config.batch_dir / "readiness_report.json",
        "readiness_md": config.batch_dir / "readiness_report.md",
        "summary_json": config.batch_dir / "finalization_summary.json",
        "summary_md": config.batch_dir / "finalization_summary.md",
        "processed": config.processed_output,
        "promotion_report": config.promotion_report,
        "promotion_rejected": rejected_path_for(config.processed_output),
        "release_dir": config.release_output_dir / config.release_name,
        "candidate": config.candidate_output,
        "eval_dir": config.eval_output_dir,
    }


def _preflight(config: FinalizationConfig, paths: dict[str, Path]) -> list[str]:
    errors: list[str] = []
    if (
        not config.release_name.strip() or config.release_name in {".", ".."}
        or Path(config.release_name).name != config.release_name
    ):
        errors.append("release-name must be a single non-empty path component")
    if not config.batch_dir.is_dir():
        errors.append(f"batch directory not found: {config.batch_dir}")
    for name in ("selected", "reviewed"):
        if not paths[name].is_file():
            errors.append(f"required batch file not found: {paths[name]}")
    if not config.golden_input.is_file():
        errors.append(f"golden input not found: {config.golden_input}")
    for name, path in paths.items():
        if name not in {"selected", "reviewed"} and _inside_local_data(path):
            errors.append(f"output must not be inside .local_data: {path}")
    if _inside_local_data(config.batch_dir):
        errors.append(f"batch directory must not be inside .local_data: {config.batch_dir}")
    if _inside_local_data(config.golden_input):
        errors.append(f"golden input must not be inside .local_data: {config.golden_input}")
    managed_paths = {name: path for name, path in paths.items() if name not in {"selected", "reviewed"}}
    managed = {name: path.resolve() for name, path in managed_paths.items()}
    by_path: dict[Path, list[str]] = {}
    for name, path in managed.items():
        by_path.setdefault(path, []).append(name)
    for path, names in by_path.items():
        if len(names) > 1:
            errors.append(f"managed output paths collide ({', '.join(sorted(names))}): {path}")
    protected = [config.batch_dir, paths["selected"], paths["reviewed"], config.golden_input]
    for name, path in managed.items():
        if any(path == item.resolve() for item in protected):
            errors.append(f"managed output must not overwrite an input ({name}): {path}")
    for directory_name in ("release_dir", "eval_dir"):
        directory = paths[directory_name]
        if directory.is_symlink():
            errors.append(f"managed output directory must not be a symlink: {directory}")
        else:
            symlink_ancestor = _symlink_in_ancestry(directory.parent)
            if symlink_ancestor is not None:
                errors.append(
                    f"managed output directory ancestry must not contain a symlink: {symlink_ancestor}"
                )
        resolved_directory = directory.resolve()
        if _is_filesystem_root(directory):
            errors.append(f"managed output directory must not be a filesystem root: {directory}")
        if resolved_directory == REPO_ROOT:
            errors.append(f"managed output directory must not be the repository root: {directory}")
        if resolved_directory == Path.home().resolve():
            errors.append(f"managed output directory must not be the home directory: {directory}")
        if any(_is_within(item, directory) for item in protected):
            errors.append(f"{directory_name} must not contain an input path: {directory}")
    if _is_within(paths["release_dir"], paths["eval_dir"]) or _is_within(paths["eval_dir"], paths["release_dir"]):
        errors.append("release and evaluation output directories must not overlap")
    for name, path in managed.items():
        if name in {"release_dir", "eval_dir"}:
            continue
        if _is_within(path, paths["release_dir"]) or _is_within(path, paths["eval_dir"]):
            errors.append(f"managed output must not be nested inside release/evaluation directories: {path}")
    if not config.force:
        for name, path in managed_paths.items():
            if _exists_or_symlink(path):
                errors.append(f"output already exists ({name}); use --force to replace it: {path}")
    else:
        for name, path in managed_paths.items():
            expects_directory = name in {"release_dir", "eval_dir"}
            if _exists_or_symlink(path) and expects_directory != path.is_dir():
                expected = "directory" if expects_directory else "file"
                errors.append(f"existing managed output is not a {expected} ({name}): {path}")
    return errors


def _remove_file(path: Path) -> None:
    if _exists_or_symlink(path):
        if not path.is_symlink() and not path.is_file():
            raise ValueError(f"expected generated file path but found non-file: {path}")
        path.unlink()


def _remove_dir(path: Path, protected: list[Path]) -> None:
    if _exists_or_symlink(path):
        if path.is_symlink():
            raise ValueError(f"managed output directory must not be a symlink: {path}")
        if not path.is_dir():
            raise ValueError(f"expected generated directory path but found non-directory: {path}")
        if _inside_local_data(path):
            raise ValueError(f"output must not be inside .local_data: {path}")
        symlink_ancestor = _symlink_in_ancestry(path.parent)
        if symlink_ancestor is not None:
            raise ValueError(
                f"managed output directory ancestry must not contain a symlink: {symlink_ancestor}"
            )
        resolved = path.resolve()
        if _is_filesystem_root(path) or resolved in {REPO_ROOT, Path.home().resolve()}:
            raise ValueError(f"refusing to remove dangerous managed output directory: {path}")
        if any(_is_within(item, path) for item in protected):
            raise ValueError(f"managed output directory must not contain an input path: {path}")
        shutil.rmtree(path)


def _cleanup_reports(paths: dict[str, Path]) -> None:
    for name in ("readiness_json", "readiness_md", "summary_json", "summary_md"):
        _remove_file(paths[name])


def _cleanup_pipeline_outputs(config: FinalizationConfig, paths: dict[str, Path]) -> None:
    for name in ("processed", "promotion_report", "promotion_rejected", "candidate"):
        _remove_file(paths[name])
    protected = [config.batch_dir, paths["selected"], paths["reviewed"], config.golden_input]
    _remove_dir(paths["release_dir"], protected)
    _remove_dir(paths["eval_dir"], protected)


def _empty_summary(config: FinalizationConfig, paths: dict[str, Path]) -> dict[str, Any]:
    return {
        "ok": False,
        "batch_dir": str(config.batch_dir),
        "readiness": {"all_rows_ready": False, "ready_rows": 0},
        "promotion": {"accepted_rows": 0, "rejected_rows": 0},
        "release": {"release_name": config.release_name, "output_dir": str(paths["release_dir"])},
        "evaluation": {"mean_score": 0.0, "rows_evaluated": 0},
        "outputs": {
            "processed_output": str(config.processed_output),
            "promotion_report": str(config.promotion_report),
            "release_dir": str(paths["release_dir"]),
            "candidate_output": str(config.candidate_output),
            "eval_output_dir": str(config.eval_output_dir),
        },
        "errors": [],
        "warnings": [],
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    status = "passed" if summary["ok"] else "stopped"
    errors = "\n".join(f"- {item}" for item in summary["errors"]) or "- none"
    warnings = "\n".join(f"- {item}" for item in summary["warnings"]) or "- none"
    return f"""# Reviewed batch finalization summary

Finalization **{status}**.

## Pipeline status

- Readiness all rows ready: {summary['readiness']['all_rows_ready']}
- Readiness ready rows: {summary['readiness']['ready_rows']}
- Promoted rows: {summary['promotion']['accepted_rows']}
- Rejected rows: {summary['promotion']['rejected_rows']}
- Release: `{summary['release']['output_dir']}`
- Baseline evaluation: `{summary['outputs']['eval_output_dir']}`
- Rows evaluated: {summary['evaluation']['rows_evaluated']}
- Mean score: {summary['evaluation']['mean_score']}

## Errors

{errors}

## Warnings

{warnings}

## Local-only notice

These generated outputs remain local until a human intentionally approves publishing or committing specific artifacts. Finalization does not replace human review and does not scientifically certify RTL correctness or dataset quality.

Manually inspect the promotion report, release manifest/dataset card, evaluation metrics, provenance, and licenses before any publication decision.
"""


def _write_summary(summary: dict[str, Any], paths: dict[str, Path]) -> None:
    paths["summary_json"].parent.mkdir(parents=True, exist_ok=True)
    paths["summary_json"].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    paths["summary_md"].write_text(_summary_markdown(summary), encoding="utf-8", newline="\n")


def _stop(summary: dict[str, Any], paths: dict[str, Path], error: str, warnings: list[str] | None = None) -> dict[str, Any]:
    summary["errors"].append(error)
    if warnings:
        summary["warnings"].extend(warnings)
    _write_summary(summary, paths)
    return summary


def finalize_batch(config: FinalizationConfig) -> dict[str, Any]:
    paths = _paths(config)
    summary = _empty_summary(config, paths)
    preflight_errors = _preflight(config, paths)
    if preflight_errors:
        summary["errors"].extend(preflight_errors)
        return summary
    try:
        if config.force:
            _cleanup_reports(paths)
        loaded = load_review_files(paths["selected"], paths["reviewed"])
        readiness = check_review_readiness(
            loaded.selected_rows, loaded.reviewed_rows,
            selected_validation_errors=loaded.selected_errors_by_id,
            reviewed_validation_errors=loaded.reviewed_errors_by_id,
            selected_file_errors=loaded.selected_errors,
            reviewed_file_errors=loaded.reviewed_errors,
            selected_file_warnings=loaded.selected_warnings,
            reviewed_file_warnings=loaded.reviewed_warnings,
        )
        write_readiness_reports(readiness, paths["readiness_json"], paths["readiness_md"])
        summary["readiness"] = {
            "all_rows_ready": readiness["all_rows_ready"],
            "ready_rows": readiness["ready_rows"],
            "needs_work_rows": readiness["needs_work_rows"],
        }
        summary["warnings"].extend(readiness["warnings"])
        if not readiness["all_rows_ready"]:
            return _stop(summary, paths, "readiness check failed; no rows were promoted")

        if config.force:
            _cleanup_pipeline_outputs(config, paths)
        reviewed_rows, load_errors = load_rows(paths["reviewed"])
        if load_errors:
            return _stop(summary, paths, "reviewed rows could not be loaded: " + "; ".join(load_errors))
        promotion, promotion_code = promote_rows(
            reviewed_rows,
            config.processed_output,
            config.promotion_report,
            PromotionConfig(target_status="validated", allow_stub_answer=False, strict=True),
        )
        summary["promotion"] = {
            "accepted_rows": promotion["accepted_rows"],
            "rejected_rows": promotion["rejected_rows"],
        }
        summary["warnings"].extend(promotion["warnings"])
        if promotion_code or not promotion["ok"] or promotion["rejected_rows"]:
            return _stop(summary, paths, "strict promotion failed: " + "; ".join(promotion["errors"] or ["one or more rows rejected"]))
        promoted_validation = validate_dataset_file(config.processed_output, strict=True)
        if not promoted_validation.ok:
            validation_errors = [item.format() for item in promoted_validation.errors + promoted_validation.warnings]
            return _stop(summary, paths, "promoted output validation failed: " + "; ".join(validation_errors))

        release, release_code = build_release(ReleaseConfig(
            release_name=config.release_name,
            output_root=config.release_output_dir,
            input_paths=[config.golden_input, config.processed_output],
            seed=config.seed,
            allow_source_overlap=config.allow_source_overlap,
            strict=True,
        ))
        summary["release"] = {
            "release_name": config.release_name,
            "output_dir": release["output_dir"],
            "accepted_rows": release["accepted_rows"],
            "test_rows": release["test_rows"],
        }
        summary["warnings"].extend(release["warnings"])
        if release_code or not release["ok"]:
            return _stop(summary, paths, "release assembly failed: " + "; ".join(release["errors"] or ["unknown release error"]))

        test_split = paths["release_dir"] / "test.jsonl"
        candidates, candidate_code = make_candidates(test_split, config.candidate_output)
        summary["warnings"].extend(candidates["warnings"])
        if candidate_code or not candidates["ok"]:
            return _stop(summary, paths, "baseline candidate generation failed: " + "; ".join(candidates["errors"] or ["unknown candidate error"]))

        dataset_rows, dataset_errors = load_dataset_rows(test_split)
        candidate_result = load_candidate_answers(config.candidate_output)
        if dataset_errors or candidate_result.errors:
            return _stop(summary, paths, "evaluation inputs failed validation: " + "; ".join(dataset_errors + candidate_result.errors))
        evaluation, evaluation_code = evaluate_dataset(
            dataset_rows,
            candidate_result.candidates,
            candidate_result.rows,
            config.eval_output_dir,
            test_split,
            config.candidate_output,
            strict=True,
        )
        summary["evaluation"] = {
            "mean_score": evaluation["mean_score"],
            "rows_evaluated": evaluation["matched_rows"],
            "safety_failures": evaluation["safety_failures"],
        }
        summary["warnings"].extend(evaluation["warnings"])
        if evaluation_code or not evaluation["ok"]:
            return _stop(summary, paths, "baseline evaluation failed: " + "; ".join(evaluation["errors"] or ["unknown evaluation error"]))
        summary["ok"] = True
        _write_summary(summary, paths)
        return summary
    except (OSError, ValueError) as exc:
        return _stop(summary, paths, f"finalization failed: {exc}")


def _print_text(summary: dict[str, Any]) -> None:
    print("Reviewed batch finalization completed." if summary["ok"] else "Reviewed batch finalization stopped.")
    print(f"Batch: {summary['batch_dir']}")
    print(f"Ready rows: {summary['readiness']['ready_rows']}")
    print(f"Promoted rows: {summary['promotion']['accepted_rows']}")
    print(f"Release: {summary['release']['output_dir']}")
    print(f"Evaluation: {summary['outputs']['eval_output_dir']}")
    if summary["errors"]:
        print("Errors:")
        for error in summary["errors"]:
            print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", required=True, type=Path)
    parser.add_argument("--processed-output", required=True, type=Path)
    parser.add_argument("--promotion-report", required=True, type=Path)
    parser.add_argument("--release-name", required=True)
    parser.add_argument("--release-output-dir", required=True, type=Path)
    parser.add_argument("--candidate-output", required=True, type=Path)
    parser.add_argument("--eval-output-dir", required=True, type=Path)
    parser.add_argument("--golden-input", type=Path, default=Path("data/golden/golden_v0.1.jsonl"))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--allow-source-overlap", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = finalize_batch(FinalizationConfig(
        batch_dir=args.batch_dir,
        processed_output=args.processed_output,
        promotion_report=args.promotion_report,
        release_name=args.release_name,
        release_output_dir=args.release_output_dir,
        candidate_output=args.candidate_output,
        eval_output_dir=args.eval_output_dir,
        golden_input=args.golden_input,
        seed=args.seed,
        allow_source_overlap=args.allow_source_overlap,
        force=args.force,
    ))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_text(summary)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
