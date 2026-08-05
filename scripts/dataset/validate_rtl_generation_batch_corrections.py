"""Validate a bounded correction manifest and authored testbenches statically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import (
    MANIFEST_SCHEMA_VERSION,
    ROW_SCHEMA_VERSION,
    _load_json,
    _load_jsonl,
    sha256_file,
    static_testbench_audit,
)
from scripts.dataset.rtl_generation_batch_selection import (
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    SELECTION_SCHEMA_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
)
from scripts.dataset.rtl_generation_inventory import source_tree_sha256


def _write_exclusive(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    path.chmod(0o600)


def validate_batch(
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
    source_root: Path,
    split_path: Path,
    correction_root: Path,
    manifest_path: Path,
) -> tuple[dict, int]:
    errors: list[str] = []
    try:
        inventory = {row["source_id"]: row for row in _load_jsonl(inventory_path)}
        selection = _load_json(selection_path)
        manifest_rows = _load_jsonl(manifest_path)
        split = _load_json(split_path)
        ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {"schema_version": "rtl_verification_asset_correction_static_validation_v0.1", "ok": False, "errors": [str(exc)]}, 1

    actual_source_tree, symlinks, tree_errors = source_tree_sha256(source_root)
    errors.extend(tree_errors)
    if symlinks:
        errors.append("source tree contains symlinks")
    if actual_source_tree != SOURCE_TREE_SHA256:
        errors.append("source tree hash mismatch")
    if sha256_file(inventory_path) != BASE_INVENTORY_SHA256:
        errors.append("inventory hash mismatch")
    if sha256_file(split_path) != BASE_SPLIT_SHA256:
        errors.append("split hash mismatch")
    if sha256_file(ids_path) != selection.get("selection_ids_sha256"):
        errors.append("selection ID hash mismatch")
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION:
        errors.append("selection schema mismatch")
    if selection.get("source_commit") != SOURCE_COMMIT:
        errors.append("selection source commit mismatch")
    if selection.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        errors.append("selection source tree mismatch")
    if selection.get("base_inventory_sha256") != BASE_INVENTORY_SHA256:
        errors.append("selection inventory binding mismatch")
    if selection.get("base_split_sha256") != BASE_SPLIT_SHA256:
        errors.append("selection split binding mismatch")
    if selection.get("ok") is not True:
        errors.append("selection report is not successful")
    if selection.get("split") != "train":
        errors.append("selection is not train-only")
    if len(ids) < 20 or len(ids) > 40:
        errors.append("selection is outside the bounded 20-40 task range")
    selection_rows = selection.get("rows")
    if not isinstance(selection_rows, list) or [row.get("source_id") for row in selection_rows if isinstance(row, dict)] != ids:
        errors.append("selection report does not preserve the pinned task order")
    if selection.get("selected_count") != len(ids):
        errors.append("selection report count does not match the selected task count")
    correction_version = selection.get("correction_version")
    if not isinstance(correction_version, str) or not correction_version:
        errors.append("selection correction version is invalid")
    if [row.get("source_id") for row in manifest_rows] != ids:
        errors.append("pinned task order mismatch")
    if len(manifest_rows) != len(ids):
        errors.append("manifest row count does not match the selected task count")
    if len({row.get("source_id") for row in manifest_rows}) != len(manifest_rows):
        errors.append("manifest contains duplicate source IDs")
    expected_train = set(split.get("splits", {}).get("train", []))
    for index, (source_id, row) in enumerate(zip(ids, manifest_rows), 1):
        source = inventory.get(source_id)
        if source is None:
            errors.append(f"row {index}: source ID missing from inventory")
            continue
        expected_fields = (
            ("schema_version", ROW_SCHEMA_VERSION),
            ("source_id", source_id),
            ("task_id", source.get("task_id")),
            ("split", "train"),
            ("source_dataset", "VerilogEval"),
            ("top_module", "TopModule"),
            ("upstream_commit", SOURCE_COMMIT),
            ("source_tree_sha256", SOURCE_TREE_SHA256),
            ("frozen_split_sha256", BASE_SPLIT_SHA256),
            ("correction_version", correction_version),
            ("original_prompt_sha256", source.get("source_prompt_sha256")),
            ("original_reference_rtl_sha256", source.get("reference_rtl_sha256")),
            ("original_testbench_sha256", source.get("testbench_sha256")),
            ("support_files", []),
            ("reference_modified", False),
            ("reference_copied_to_support", False),
            ("qualification_status", "pending_isolated_qualification"),
            ("verification_readiness", "pending_qualification"),
            ("dependency_closure", "passed"),
        )
        for field, expected in expected_fields:
            if row.get(field) != expected:
                errors.append(f"row {index}: {field} mismatch")
        if correction_version == "assetfix_v004":
            for field, expected in (
                ("public_specification_sha256", source.get("source_prompt_sha256")),
                ("selection_ids_sha256", sha256_file(ids_path)),
                ("selection_report_sha256", sha256_file(selection_path)),
            ):
                if row.get(field) != expected:
                    errors.append(f"row {index}: {field} mismatch")
        if source_id not in expected_train:
            errors.append(f"row {index}: source ID is outside train split")
        correction_path = correction_root / row.get("testbench_path", "")
        if not correction_path.is_file() or correction_path.is_symlink():
            errors.append(f"row {index}: corrected testbench path invalid")
            continue
        content = correction_path.read_bytes()
        if sha256_file(correction_path) != row.get("corrected_testbench_sha256"):
            errors.append(f"row {index}: corrected testbench hash mismatch")
        audit, audit_errors = static_testbench_audit(content)
        if audit_errors:
            errors.extend(f"row {index}: {error}" for error in audit_errors)
        if row.get("static_audit") != audit:
            errors.append(f"row {index}: static audit does not match manifest")
        contracts = row.get("mutation_contracts")
        if not isinstance(contracts, list) or not any(item.get("kind") == "positive" for item in contracts if isinstance(item, dict)):
            errors.append(f"row {index}: positive qualification contract missing")
        if not isinstance(contracts, list) or not any(item.get("kind") == "negative" for item in contracts if isinstance(item, dict)):
            errors.append(f"row {index}: negative qualification contract missing")
    result = {
        "schema_version": "rtl_verification_asset_correction_static_validation_v0.1",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": actual_source_tree,
        "inventory_sha256": sha256_file(inventory_path),
        "split_sha256": sha256_file(split_path),
        "selection_ids_sha256": sha256_file(ids_path),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_row_count": len(manifest_rows),
        "static_dependency_closure_passed": not errors,
        "qualification_status": "pending_isolated_qualification",
        "reference_rtl_copied": False,
        "errors": sorted(set(errors)),
    }
    return {**result, "ok": not errors}, 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result, code = validate_batch(
            args.inventory, args.selection, args.ids, args.source_root,
            args.split, args.correction_root, args.manifest,
        )
        _write_exclusive(args.report, result)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        result, code = {"schema_version": "rtl_verification_asset_correction_static_validation_v0.1", "ok": False, "errors": [str(exc)]}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
