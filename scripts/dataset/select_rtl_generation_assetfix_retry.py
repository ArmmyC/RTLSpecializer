#!/usr/bin/env python3
"""Create a public-metadata-only qualification retry selection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import (
    is_qualification_retry_selection,
)
from scripts.dataset.rtl_generation_batch_selection import (
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    SELECTION_SCHEMA_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
)


SOURCE_IDS = (
    "Prob092_gatesv100",
    "Prob094_gatesv",
    "Prob101_circuit4",
    "Prob112_always_case2",
)
CORRECTION_VERSION = "assetfix_v006_retry_02"


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
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def create_selection(
    *,
    inventory_path: Path,
    split_path: Path,
    ids_path: Path,
    selection_path: Path,
    parent_run_id: str,
    parent_binding_path: Path,
    parent_failure_report_path: Path,
    parent_qualification_report_path: Path,
) -> dict[str, Any]:
    inventory = _load_jsonl(inventory_path)
    inventory_by_id = {row.get("source_id"): row for row in inventory}
    split = _load_json(split_path)
    train_ids = set((split.get("splits") or {}).get("train", []))
    parent_binding = _load_json(parent_binding_path)

    if _sha256(inventory_path) != BASE_INVENTORY_SHA256:
        raise ValueError("base inventory hash mismatch")
    if _sha256(split_path) != BASE_SPLIT_SHA256:
        raise ValueError("frozen split hash mismatch")
    if selection_path.exists() or ids_path.exists():
        raise ValueError("retry selection output already exists")
    if len(set(SOURCE_IDS)) != len(SOURCE_IDS):
        raise ValueError("retry selection contains duplicate IDs")

    rows: list[dict[str, Any]] = []
    for order, source_id in enumerate(SOURCE_IDS, 1):
        source = inventory_by_id.get(source_id)
        if source is None:
            raise ValueError(f"source ID is absent from inventory: {source_id}")
        if source_id not in train_ids:
            raise ValueError(f"source ID is outside the frozen train split: {source_id}")
        if source.get("verification_readiness") != "needs_testbench":
            raise ValueError(f"source is not an unresolved asset row: {source_id}")
        rows.append({
            "selection_order": order,
            "source_id": source_id,
            "task_id": source.get("task_id"),
            "split": "train",
            "current_readiness": source.get("verification_readiness"),
            "blocking_reason": source.get("blocking_reason"),
            "design_family": source.get("design_family"),
            "selection_reason": "replace a public-oracle expectation defect identified by read-only candidate diagnosis",
            "retry_classification": "testbench_behavior_defect",
        })

    ids_bytes = ("\n".join(SOURCE_IDS) + "\n").encode("utf-8")
    _write_exclusive(ids_path, ids_bytes)
    ids_hash = _sha256(ids_path)

    selection = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "selection_kind": "qualification_retry",
        "selection_id": "verilog_eval_assetfix_v006_retry_02",
        "source_dataset": "VerilogEval",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "base_inventory_sha256": BASE_INVENTORY_SHA256,
        "base_split_sha256": BASE_SPLIT_SHA256,
        "correction_version": CORRECTION_VERSION,
        "split": "train",
        "selected_count": len(rows),
        "selection_ids_sha256": ids_hash,
        "parent_run_id": parent_run_id,
        "parent_qualification_report_sha256": parent_binding.get("qualification_report_sha256"),
        "parent_qualification_evidence_sha256": parent_binding.get("qualification_evidence_sha256"),
        "parent_qualified_subset_binding_sha256": _sha256(parent_binding_path),
        "parent_failure_report_sha256": _sha256(parent_failure_report_path),
        "parent_qualification_report_file_sha256": _sha256(parent_qualification_report_path),
        "retry_reason": "public oracle expectation defects identified without reading private HDL",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "rows": rows,
        "ok": True,
        "errors": [],
    }
    if not is_qualification_retry_selection(selection):
        raise ValueError("selection does not satisfy the qualification-retry binding")
    _write_exclusive(
        selection_path,
        (json.dumps(selection, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "ok": True,
        "selection": selection_path.as_posix(),
        "ids": ids_path.as_posix(),
        "selection_sha256": _sha256(selection_path),
        "selection_ids_sha256": ids_hash,
        "source_ids": list(SOURCE_IDS),
        "selected_count": len(rows),
        "reference_rtl_supplied": False,
        "support_file_count": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument("--parent-binding", required=True, type=Path)
    parser.add_argument("--parent-failure-report", required=True, type=Path)
    parser.add_argument("--parent-qualification-report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = create_selection(
            inventory_path=args.inventory,
            split_path=args.split,
            ids_path=args.ids,
            selection_path=args.selection,
            parent_run_id=args.parent_run_id,
            parent_binding_path=args.parent_binding,
            parent_failure_report_path=args.parent_failure_report,
            parent_qualification_report_path=args.parent_qualification_report,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
