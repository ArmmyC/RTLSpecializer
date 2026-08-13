#!/usr/bin/env python3
"""Statically validate a small public-specification asset retry overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import (
    is_qualification_retry_selection,
    static_testbench_audit,
)
from scripts.dataset.rtl_generation_batch_selection import (
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object: {path}")
            rows.append(value)
    return rows


def _require_private_file(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"overlay entry is not a regular file: {path}")
    if metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ValueError(f"overlay file permission/link contract failed: {path}")


def _validate_candidate(path: Path) -> list[str]:
    _require_private_file(path)
    text = path.read_text(encoding="utf-8")
    lowered = text.casefold()
    forbidden = (
        "`include",
        "refmodule",
        "reference.sv",
        "_ref.sv",
        "/home/",
        "/tmp/",
        "/root/",
        "private_assets",
        "testbench",
        "candidate_evidence",
    )
    return [f"forbidden candidate marker: {marker}" for marker in forbidden if marker in lowered]


def validate(
    *,
    inventory_path: Path,
    split_path: Path,
    ids_path: Path,
    selection_path: Path,
    correction_root: Path,
    manifest_path: Path,
    authoring_manifest_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists() or output_path.is_symlink():
        raise ValueError(f"refusing to replace static report: {output_path}")
    inventory = {row["source_id"]: row for row in _load_jsonl(inventory_path)}
    split = _load_json(split_path)
    selection = _load_json(selection_path)
    manifest = _load_jsonl(manifest_path)
    authoring = _load_jsonl(authoring_manifest_path)
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    errors: list[str] = []
    if _sha256(inventory_path) != BASE_INVENTORY_SHA256:
        errors.append("base inventory hash mismatch")
    if _sha256(split_path) != BASE_SPLIT_SHA256:
        errors.append("frozen split hash mismatch")
    if selection.get("source_commit") != SOURCE_COMMIT:
        errors.append("source commit mismatch")
    if selection.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        errors.append("source tree hash mismatch")
    if selection.get("base_inventory_sha256") != BASE_INVENTORY_SHA256:
        errors.append("selection inventory binding mismatch")
    if selection.get("base_split_sha256") != BASE_SPLIT_SHA256:
        errors.append("selection split binding mismatch")
    if selection.get("selection_ids_sha256") != _sha256(ids_path):
        errors.append("selection ID hash mismatch")
    if selection.get("rows") is None or [row.get("source_id") for row in selection.get("rows", [])] != ids:
        errors.append("selection order mismatch")
    if not is_qualification_retry_selection(selection):
        errors.append("selection is not an explicitly bound qualification retry")
    train_ids = set((split.get("splits") or {}).get("train", []))
    if any(source_id not in train_ids for source_id in ids):
        errors.append("selection contains a non-train source")
    if [row.get("source_id") for row in manifest] != ids:
        errors.append("correction manifest order mismatch")
    if [row.get("source_id") for row in authoring] != ids:
        errors.append("authoring manifest order mismatch")
    if len(set(ids)) != len(ids) or len(manifest) != len(ids) or len(authoring) != len(ids):
        errors.append("retry row count or uniqueness mismatch")

    correction_version = selection.get("correction_version")
    for source_id, correction, author in zip(ids, manifest, authoring):
        source = inventory.get(source_id)
        if source is None:
            errors.append(f"source missing from inventory: {source_id}")
            continue
        expected = {
            "source_id": source_id,
            "task_id": source.get("task_id"),
            "top_module": "TopModule",
            "split": "train",
            "correction_version": correction_version,
            "upstream_commit": SOURCE_COMMIT,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "frozen_split_sha256": BASE_SPLIT_SHA256,
            "original_prompt_sha256": source.get("source_prompt_sha256"),
            "original_reference_rtl_sha256": source.get("reference_rtl_sha256"),
            "original_testbench_sha256": source.get("testbench_sha256"),
            "public_specification_sha256": source.get("source_prompt_sha256"),
            "support_files": [],
            "reference_modified": False,
            "reference_copied_to_support": False,
            "qualification_status": "pending_isolated_qualification",
            "verification_readiness": "pending_qualification",
            "dependency_closure": "passed",
        }
        for field, value in expected.items():
            if correction.get(field) != value:
                errors.append(f"manifest binding mismatch: {source_id}:{field}")
        tb_path = correction_root / correction.get("testbench_path", "")
        if not tb_path.is_file() or tb_path.is_symlink():
            errors.append(f"testbench missing: {source_id}")
        else:
            _require_private_file(tb_path)
            tb_bytes = tb_path.read_bytes()
            audit, audit_errors = static_testbench_audit(tb_bytes)
            if audit_errors:
                errors.extend(f"{source_id}: {item}" for item in audit_errors)
            if correction.get("static_audit") != audit:
                errors.append(f"testbench static audit mismatch: {source_id}")
            if correction.get("corrected_testbench_sha256") != _sha256(tb_path):
                errors.append(f"testbench hash mismatch: {source_id}")
        if author.get("schema_version") != "rtl_asset_qualification_authoring_row_v0.1":
            errors.append(f"authoring schema mismatch: {source_id}")
        if author.get("task_id") != source.get("task_id") or author.get("reference_used") is not False or author.get("support_files") != []:
            errors.append(f"authoring identity/privacy mismatch: {source_id}")
        expected_negative_names = [item["name"] for item in correction.get("mutation_contracts", []) if item.get("kind") == "negative"]
        actual_negative_names = [item.get("name") for item in author.get("negative_mutations", [])]
        if actual_negative_names != expected_negative_names:
            errors.append(f"mutation order mismatch: {source_id}")
        for relative in [author.get("positive_rtl_path"), *[item.get("rtl_path") for item in author.get("negative_mutations", [])]]:
            candidate_path = correction_root / "qualification" / relative
            if not candidate_path.is_file() or candidate_path.is_symlink():
                errors.append(f"fixture missing: {source_id}:{relative}")
                continue
            errors.extend(f"{source_id}:{item}" for item in _validate_candidate(candidate_path))

    for path in correction_root.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.name.casefold() == "reference.sv"):
            errors.append(f"forbidden overlay entry: {path.name}")
        if path.is_file() and path.name != "" and stat.S_IMODE(path.stat().st_mode) != 0o600:
            errors.append(f"overlay file mode mismatch: {path}")
        if path.is_dir() and stat.S_IMODE(path.stat().st_mode) != 0o700:
            errors.append(f"overlay directory mode mismatch: {path}")

    result = {
        "schema_version": "rtl_verification_asset_correction_retry_static_validation_v0.1",
        "ok": not errors,
        "correction_version": correction_version,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_sha256": _sha256(selection_path),
        "selection_ids_sha256": _sha256(ids_path),
        "manifest_sha256": _sha256(manifest_path),
        "authoring_manifest_sha256": _sha256(authoring_manifest_path),
        "selected_source_ids": ids,
        "selected_task_count": len(ids),
        "positive_case_count": len(ids),
        "negative_case_count": len(ids) * 2,
        "dependency_closure_passed": not errors,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_status": "pending_isolated_qualification",
        "errors": sorted(set(errors)),
    }
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with output_path.open("xb") as handle:
        handle.write((json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    output_path.chmod(0o600)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--authoring-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate(
            inventory_path=args.inventory,
            split_path=args.split,
            ids_path=args.ids,
            selection_path=args.selection,
            correction_root=args.correction_root,
            manifest_path=args.manifest,
            authoring_manifest_path=args.authoring_manifest,
            output_path=args.output,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
