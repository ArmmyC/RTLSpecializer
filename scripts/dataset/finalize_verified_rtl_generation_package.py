"""Finalize a validated recovery package without rebuilding its dataset rows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any

if __package__ in {None, ""}:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_dataset import (
    _load_json,
    _output_paths,
    _write_atomic,
    validate_generation_sft_package,
)


def _load_gate(path: Path, label: str) -> dict[str, Any]:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _copy_managed_outputs(source: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for path in _output_paths(source).values():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or symlinked package output: {path.name}")
        target = destination / path.name
        shutil.copyfile(path, target)
        os.chmod(target, stat.S_IMODE(path.stat().st_mode))


def finalize_package(
    package_dir: Path,
    *,
    complete_run_report: Path,
    freeze_report: Path,
    review_report: Path,
    expected_package_id: str,
    expected_parent_package_id: str,
    expected_recovery_reason: str,
    validation_kwargs: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    try:
        if package_dir.is_symlink() or not package_dir.is_dir():
            raise ValueError("package directory is missing or symlinked")
        current_manifest = _load_gate(package_dir / "manifest.json", "manifest")
        current_lineage = current_manifest.get("lineage")
        if not isinstance(current_lineage, dict):
            raise ValueError("package lineage is missing")
        if current_lineage.get("package_id") != expected_package_id:
            raise ValueError("package ID mismatch")
        if current_lineage.get("parent_package_id") != expected_parent_package_id:
            raise ValueError("parent package ID mismatch")
        if current_lineage.get("recovery_reason") != expected_recovery_reason:
            raise ValueError("recovery reason mismatch")
        if current_lineage.get("status") != "pending_finalization" or current_lineage.get("consumable") is not False:
            raise ValueError("package is not pending finalization")

        complete = _load_gate(complete_run_report, "complete-run report")
        if complete.get("ok") is not True or complete.get("errors") not in ([], None):
            raise ValueError("complete-run validation did not pass")
        freeze = _load_gate(freeze_report, "freeze report")
        if freeze.get("package_id") != expected_package_id or freeze.get("ok") is not True:
            raise ValueError("pre-finalization freeze report is invalid")
        if not isinstance(freeze.get("package_tree_sha256"), str):
            raise ValueError("pre-finalization package tree hash is missing")
        review = _load_gate(review_report, "content review report")
        if review.get("package_id") != expected_package_id or review.get("ok") is not True:
            raise ValueError("content review did not pass")
        if review.get("row_count") != 5 or review.get("promotion_allowed") is not False:
            raise ValueError("content review is not conservative")

        temporary_parent = package_dir.parent
        temporary = Path(tempfile.mkdtemp(prefix=f".{package_dir.name}.finalize.", dir=temporary_parent))
        try:
            staged = temporary / package_dir.name
            _copy_managed_outputs(package_dir, staged)
            staged_manifest_path = staged / "manifest.json"
            staged_manifest = _load_gate(staged_manifest_path, "staged manifest")
            staged_lineage = dict(staged_manifest["lineage"])
            staged_lineage["status"] = "successful_recovery_package"
            staged_lineage["consumable"] = True
            staged_manifest["lineage"] = staged_lineage
            staged_manifest["consumable"] = True
            _write_atomic(
                staged_manifest_path,
                (json.dumps(staged_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )

            validation_path = staged / "validation_report.json"
            validation = _load_gate(validation_path, "validation report")
            validation["lineage"] = staged_lineage
            validation["consumable"] = True
            validation["finalization"] = {
                "complete_run_validation": "passed",
                "pre_finalization_freeze": "passed",
                "content_review": "passed",
            }
            _write_atomic(
                validation_path,
                (json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )

            provenance_path = staged / "provenance_report.json"
            provenance = _load_gate(provenance_path, "provenance report")
            provenance["lineage"] = staged_lineage
            provenance["consumable"] = True
            _write_atomic(
                provenance_path,
                (json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            )

            markdown_path = staged / "validation_report.md"
            markdown = markdown_path.read_text(encoding="utf-8")
            markdown += (
                "\n- Finalization: passed\n"
                "- Complete-run validation: passed\n"
                "- Content review: passed\n"
                "- Consumable: true\n"
            )
            _write_atomic(markdown_path, markdown.encode("utf-8"))

            final_report, final_code = validate_generation_sft_package(
                staged,
                **validation_kwargs,
                expected_package_id=expected_package_id,
                expected_parent_package_id=expected_parent_package_id,
                expected_recovery_reason=expected_recovery_reason,
                require_recovery_lineage=True,
                require_consumable=True,
            )
            if final_code != 0:
                return {"ok": False, "errors": final_report.get("errors", []), "validation": final_report}, 1

            for path in _output_paths(staged).values():
                os.replace(path, package_dir / path.name)
            return {
                "ok": True,
                "package_id": expected_package_id,
                "consumable": True,
                "validation": final_report,
                "finalized_files": sorted(path.name for path in _output_paths(staged).values()),
            }, 0
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--complete-run-report", required=True, type=Path)
    parser.add_argument("--freeze-report", required=True, type=Path)
    parser.add_argument("--review-report", required=True, type=Path)
    parser.add_argument("--expected-package-id", required=True)
    parser.add_argument("--expected-parent-package-id", required=True)
    parser.add_argument("--expected-recovery-reason", required=True)
    parser.add_argument("--asset-qualification", required=True, type=Path)
    parser.add_argument("--qualification-binding", required=True, type=Path)
    parser.add_argument("--base-split-manifest", required=True, type=Path)
    parser.add_argument("--readiness-split-manifest", required=True, type=Path)
    parser.add_argument("--source-acquisition", required=True, type=Path)
    parser.add_argument("--asset-correction-report", required=True, type=Path)
    parser.add_argument("--qualified-correction-manifest", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-source-tree-sha256", required=True)
    parser.add_argument("--expected-frozen-split-sha256", required=True)
    parser.add_argument("--expected-runner-profile", required=True)
    parser.add_argument("--expected-runner-image", required=True)
    args = parser.parse_args(argv)
    validation_kwargs = {
        "require_asset_qualification": True,
        "asset_qualification_path": args.asset_qualification,
        "qualification_binding_path": args.qualification_binding,
        "base_split_path": args.base_split_manifest,
        "readiness_split_path": args.readiness_split_manifest,
        "source_acquisition_path": args.source_acquisition,
        "asset_correction_report_path": args.asset_correction_report,
        "qualified_correction_manifest_path": args.qualified_correction_manifest,
        "expected_source_commit": args.expected_source_commit,
        "expected_source_tree_sha256": args.expected_source_tree_sha256,
        "expected_frozen_split_sha256": args.expected_frozen_split_sha256,
        "expected_runner_profile": args.expected_runner_profile,
        "expected_runner_image": args.expected_runner_image,
        "require_smoke_provenance": True,
    }
    result, code = finalize_package(
        args.input_dir,
        complete_run_report=args.complete_run_report,
        freeze_report=args.freeze_report,
        review_report=args.review_report,
        expected_package_id=args.expected_package_id,
        expected_parent_package_id=args.expected_parent_package_id,
        expected_recovery_reason=args.expected_recovery_reason,
        validation_kwargs=validation_kwargs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
