#!/usr/bin/env python3
"""Validate the assetfix_v010 overlay without executing any HDL."""

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

from scripts.dataset.author_rtl_generation_assetfix_v010 import (
    CORRECTION_VERSION,
    INVENTORY_SHA256,
    SOURCE_COMMIT,
    SOURCE_IDS,
    SOURCE_TREE_SHA256,
    SPLIT_SHA256,
    PUBLIC_TESTBENCHES,
    PUBLIC_FIXTURES,
)
from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit


class ValidationError(ValueError):
    """Raised for a malformed or unsafe correction overlay."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"missing or symlinked JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"missing or symlinked JSONL file: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(*, correction_root: Path, inventory_path: Path, selection_path: Path, ids_path: Path, authorization_path: Path) -> dict[str, Any]:
    if correction_root.is_symlink() or not correction_root.is_dir():
        raise ValidationError("correction root is missing or symlinked")
    if stat.S_IMODE(correction_root.lstat().st_mode) != 0o700:
        raise ValidationError("correction root mode is not 0700")
    inventory = {row["source_id"]: row for row in _load_jsonl(inventory_path)}
    selection = _load_json(selection_path)
    authorization = _load_json(authorization_path)
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if ids != list(SOURCE_IDS):
        raise ValidationError("selected IDs are not in the pinned order")
    if selection.get("selection_ids_sha256") != sha256_file(ids_path):
        raise ValidationError("selection ID hash mismatch")
    if selection.get("correction_version") != CORRECTION_VERSION:
        raise ValidationError("selection correction version mismatch")
    if authorization.get("selection_report_sha256") != sha256_file(selection_path):
        raise ValidationError("authorization selection hash mismatch")
    if authorization.get("selection_ids_sha256") != sha256_file(ids_path):
        raise ValidationError("authorization ID hash mismatch")

    manifest_rows = _load_jsonl(correction_root / "manifest.jsonl")
    authoring_rows = _load_jsonl(correction_root / "qualification" / "authoring_manifest.jsonl")
    attestation = _load_json(correction_root / "authoring_attestation.json")
    if [row.get("source_id") for row in manifest_rows] != list(SOURCE_IDS):
        raise ValidationError("manifest source order mismatch")
    if [row.get("source_id") for row in authoring_rows] != list(SOURCE_IDS):
        raise ValidationError("authoring source order mismatch")
    if attestation.get("correction_version") != CORRECTION_VERSION:
        raise ValidationError("attestation correction version mismatch")
    if attestation.get("source_commit") != SOURCE_COMMIT or attestation.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise ValidationError("attestation source identity mismatch")
    if attestation.get("inventory_sha256") != INVENTORY_SHA256 or attestation.get("frozen_split_sha256") != SPLIT_SHA256:
        raise ValidationError("attestation corpus identity mismatch")
    if attestation.get("reference_rtl_supplied") is not False or attestation.get("support_file_count") != 0:
        raise ValidationError("reference/support policy failed")

    rows_summary = []
    for manifest in manifest_rows:
        source_id = manifest["source_id"]
        source = inventory[source_id]
        if manifest.get("task_id") != source.get("task_id") or manifest.get("split") != "train":
            raise ValidationError(f"identity or split mismatch: {source_id}")
        if manifest.get("top_module") != "TopModule":
            raise ValidationError(f"top module mismatch: {source_id}")
        if manifest.get("original_prompt_sha256") != source.get("source_prompt_sha256"):
            raise ValidationError(f"prompt hash mismatch: {source_id}")
        if manifest.get("original_reference_rtl_sha256") != source.get("reference_rtl_sha256"):
            raise ValidationError(f"reference hash mismatch: {source_id}")
        if manifest.get("original_testbench_sha256") != source.get("testbench_sha256"):
            raise ValidationError(f"original testbench hash mismatch: {source_id}")
        testbench_path = correction_root / "tasks" / source_id / "testbench.sv"
        if sha256_file(testbench_path) != manifest.get("corrected_testbench_sha256"):
            raise ValidationError(f"corrected testbench hash mismatch: {source_id}")
        audit, errors = static_testbench_audit(testbench_path.read_bytes())
        if errors or audit.get("support_file_count") != 0:
            raise ValidationError(f"testbench audit failed: {source_id}: {errors}")
        if testbench_path.read_text(encoding="utf-8") != PUBLIC_TESTBENCHES[source_id]:
            raise ValidationError(f"testbench bytes differ from public authoring catalog: {source_id}")
        fixture_hashes = manifest.get("fixture_hashes")
        if not isinstance(fixture_hashes, dict) or set(fixture_hashes) != set(PUBLIC_FIXTURES[source_id]):
            raise ValidationError(f"fixture hash set mismatch: {source_id}")
        for name, content in PUBLIC_FIXTURES[source_id].items():
            fixture_path = correction_root / "qualification" / source_id / f"{name}.sv"
            if sha256_file(fixture_path) != fixture_hashes[name] or fixture_path.read_text(encoding="utf-8") != content:
                raise ValidationError(f"fixture hash/content mismatch: {source_id}:{name}")
        rows_summary.append({
            "source_id": source_id,
            "task_id": manifest["task_id"],
            "corrected_testbench_sha256": manifest["corrected_testbench_sha256"],
            "fixture_names": sorted(fixture_hashes),
            "dependency_closure": manifest.get("dependency_closure"),
        })

    for path in correction_root.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise ValidationError(f"link or special file found: {path}")
        if stat.S_ISDIR(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ValidationError(f"directory mode is not 0700: {path}")
        if stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValidationError(f"file mode is not 0600: {path}")

    return {
        "ok": True,
        "errors": [],
        "correction_version": CORRECTION_VERSION,
        "source_ids": list(SOURCE_IDS),
        "selected_count": len(SOURCE_IDS),
        "positive_case_count": len(SOURCE_IDS),
        "negative_case_count": len(SOURCE_IDS) * 2,
        "dependency_closure": "passed",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_status": "pending_isolated_qualification",
        "rows": rows_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = validate(correction_root=args.correction_root, inventory_path=args.inventory, selection_path=args.selection, ids_path=args.ids, authorization_path=args.authorization)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
