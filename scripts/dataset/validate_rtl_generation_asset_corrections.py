"""Validate a reference-free five-row RTL verification asset correction set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_asset_corrections import (
    CORRECTION_VALIDATION_SCHEMA_VERSION,
    correction_manifest_report,
    load_correction_manifest,
    sha256_file,
    validate_correction_rows,
)
from scripts.dataset.rtl_generation_inventory import (
    _load_inventory,
    audit_source_inventory,
    validate_generation_split,
    write_inventory_outputs,
)
from scripts.dataset.rtl_generation_preparation import discover_source_rows


def _load_selection(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(f"selection must not be a symlink: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("selection must be an object")
    return value


def _ensure_absent(paths: list[Path]) -> list[str]:
    return [f"output already exists: {path}" for path in paths if path.exists() or path.is_symlink()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--base-inventory", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--corrected-inventory", required=True, type=Path)
    parser.add_argument("--corrected-acquisition", required=True, type=Path)
    parser.add_argument("--corrected-audit", required=True, type=Path)
    parser.add_argument("--corrected-missing", required=True, type=Path)
    parser.add_argument("--corrected-duplicates", required=True, type=Path)
    parser.add_argument("--corrected-license", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)

    errors: list[str] = []
    try:
        base_inventory = _load_inventory(args.base_inventory)
        selection = _load_selection(args.selection)
        split_report, split_code = validate_generation_split(
            args.base_inventory,
            args.split_manifest,
            expected_seed=7,
        )
        correction_rows, correction_errors = load_correction_manifest(
            args.correction_manifest,
            args.correction_root,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        result = {"schema_version": CORRECTION_VALIDATION_SCHEMA_VERSION, "ok": False, "errors": [str(exc)]}
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    errors.extend(split_report.get("errors", []) if split_code else [])
    errors.extend(correction_errors)
    if selection.get("schema_version") != "rtl_verification_asset_correction_selection_v0.1":
        errors.append("wrong correction selection schema version")
    if selection.get("base_inventory_sha256") != sha256_file(args.base_inventory):
        errors.append("selection base inventory hash mismatch")
    if selection.get("base_split_sha256") != sha256_file(args.split_manifest):
        errors.append("selection base split hash mismatch")
    selection_rows = selection.get("rows")
    if not isinstance(selection_rows, list):
        errors.append("correction selection rows must be an array")
        selection_rows = []
    if len(selection_rows) != 5:
        errors.append("correction selection must contain exactly five rows")
    base_by_id = {row["source_id"]: row for row in base_inventory}
    train_ids = set(json.loads(args.split_manifest.read_text(encoding="utf-8")).get("splits", {}).get("train", []))
    for index, selected in enumerate(selection_rows, 1):
        if not isinstance(selected, dict):
            errors.append(f"selection row {index} must be an object")
            continue
        source_id = selected.get("source_id")
        base = base_by_id.get(source_id)
        if selected.get("split") != "train":
            errors.append(f"selection row {index}: split must be train")
        if selected.get("current_readiness") != "needs_testbench":
            errors.append(f"selection row {index}: current readiness must be needs_testbench")
        if selected.get("blocking_reason") != "reference_only_dependency":
            errors.append(f"selection row {index}: blocking reason must be reference_only_dependency")
        if source_id not in train_ids:
            errors.append(f"selection row {index}: source ID is outside the train split")
        if base is None:
            errors.append(f"selection row {index}: source ID is absent from the base inventory")
            continue
        if selected.get("task_id") != base.get("task_id"):
            errors.append(f"selection row {index}: task ID differs from the base inventory")
        if selected.get("design_family") != base.get("design_family"):
            errors.append(f"selection row {index}: design family differs from the base inventory")
    selected_ids = {row.get("source_id") for row in selection_rows if isinstance(row, dict)}
    correction_ids = {row.get("source_id") for row in correction_rows}
    if selected_ids != correction_ids:
        errors.append("selection and correction manifest source IDs differ")
    if len(correction_rows) != 5:
        errors.append("correction manifest must contain exactly five rows")

    source_rows, source_errors = discover_source_rows(args.input)
    errors.extend(source_errors)
    for row in source_rows:
        existing = row.provenance.get("source_commit") if isinstance(row.provenance, dict) else None
        if existing not in (None, "", args.source_commit):
            errors.append(f"source commit mismatch for source_id: {row.source_id}")
        row.source_commit = args.source_commit
        row.provenance["source_commit"] = args.source_commit

    validated_rows, validation_errors = validate_correction_rows(
        source_rows,
        correction_rows,
        args.correction_root,
        source_commit=args.source_commit,
    )
    errors.extend(validation_errors)

    expected_ids = {row["source_id"] for row in base_inventory}
    if {row.source_id for row in source_rows} != expected_ids:
        errors.append("source tree IDs differ from base inventory IDs")

    output_paths = [
        args.corrected_inventory,
        args.corrected_acquisition,
        args.corrected_audit,
        args.corrected_missing,
        args.corrected_duplicates,
        args.corrected_license,
        args.report,
    ]
    errors.extend(_ensure_absent(output_paths))

    audit_report = None
    corrected_inventory: list[dict] = []
    acquisition: dict = {}
    if not errors:
        audit_report, corrected_inventory, acquisition, audit_code = audit_source_inventory(
            args.input,
            args.source_root,
            args.source_commit,
            correction_manifest=args.correction_manifest,
            correction_root=args.correction_root,
        )
        errors.extend(audit_report.get("errors", []))
        if audit_code:
            errors.append("corrected overlay inventory audit failed")
        if len(corrected_inventory) != 156:
            errors.append(f"corrected inventory row count is {len(corrected_inventory)}, expected 156")
        readiness_counts = {}
        for row in corrected_inventory:
            readiness_counts[row["verification_readiness"]] = readiness_counts.get(row["verification_readiness"], 0) + 1
        if readiness_counts.get("executable_ready", 0) != 5:
            errors.append(f"corrected executable_ready count is {readiness_counts.get('executable_ready', 0)}, expected 5")
        if readiness_counts.get("needs_testbench", 0) != 151:
            errors.append(f"corrected needs_testbench count is {readiness_counts.get('needs_testbench', 0)}, expected 151")
        if len({row["source_id"] for row in corrected_inventory}) != 156:
            errors.append("corrected inventory contains duplicate source IDs")
        if [row["task_id"] for row in corrected_inventory if row["source_id"] in correction_ids] != [
            row["task_id"] for row in base_inventory if row["source_id"] in correction_ids
        ]:
            errors.append("corrected task IDs changed")
    else:
        readiness_counts = {}

    if not errors:
        write_inventory_outputs(
            audit_report,
            corrected_inventory,
            acquisition,
            args.corrected_inventory,
            args.corrected_acquisition,
            audit_path=args.corrected_audit,
            missing_path=args.corrected_missing,
            duplicates_path=args.corrected_duplicates,
            license_path=args.corrected_license,
        )
        selection_for_report = dict(selection)
        selection_for_report["selection_report_sha256"] = sha256_file(args.selection)
        final_rows = []
        by_validated = {row["source_id"]: row for row in validated_rows}
        for row in correction_rows:
            final_rows.append(by_validated.get(row["source_id"], row))
        report = correction_manifest_report(
            selection_for_report,
            final_rows,
            source_commit=args.source_commit,
            base_inventory_sha256=sha256_file(args.base_inventory),
            base_split_sha256=sha256_file(args.split_manifest),
            correction_manifest_sha256=sha256_file(args.correction_manifest),
            corrected_inventory_sha256=sha256_file(args.corrected_inventory),
            source_tree_sha256=acquisition.get("source_tree_sha256"),
            inventory_counts=readiness_counts,
            errors=[],
        )
        report["ok"] = True
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = {
            "schema_version": CORRECTION_VALIDATION_SCHEMA_VERSION,
            "ok": True,
            "correction_manifest": str(args.correction_manifest),
            "corrected_inventory": str(args.corrected_inventory),
            "corrected_inventory_sha256": sha256_file(args.corrected_inventory),
            "readiness_counts": readiness_counts,
            "rows": len(final_rows),
            "negative_mutation_execution": "deferred_to_isolated_verification",
            "errors": [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    result = {
        "schema_version": CORRECTION_VALIDATION_SCHEMA_VERSION,
        "ok": False,
        "rows": len(correction_rows),
        "errors": sorted(set(errors)),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
