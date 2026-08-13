#!/usr/bin/env python3
"""Prepare a bounded public-specification retry for two remaining assets.

This command records a read-only diagnosis and prepares immutable selection
and authorization metadata for ``assetfix_v010``.  It does not read private
RTL or testbench content and does not execute RTLBench or any HDL tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
CORRECTION_VERSION = "assetfix_v010"
SELECTION_ID = "verilog_eval_assetfix_v010_remaining_asset_retry"

SELECTED_SOURCE_IDS = (
    "Prob149_ece241_2013_q4",
    "Prob139_2013_q2bfsm",
)
EXCLUDED_CANDIDATE_EXHAUSTED_IDS = (
    "Prob137_fsm_serial",
    "Prob146_fsm_serialdata",
)
REMAINING_SOURCE_IDS = (
    "Prob149_ece241_2013_q4",
    "Prob137_fsm_serial",
    "Prob139_2013_q2bfsm",
    "Prob146_fsm_serialdata",
)

PARENT_ARTIFACTS = {
    "Prob149_ece241_2013_q4": {
        "run_id": "pilot_017_assetfix_v008_final_train_remainder",
        "qualification_evidence": "data/runs/manual_rtl_teacher/pilot_017_assetfix_v008_final_train_remainder/reports/qualification_evidence.jsonl",
        "qualification_report": "data/runs/manual_rtl_teacher/pilot_017_assetfix_v008_final_train_remainder/reports/qualification_report.json",
        "qualification_freeze": "data/runs/manual_rtl_teacher/pilot_017_assetfix_v008_final_train_remainder/reports/qualification_freeze.json",
    },
    "Prob139_2013_q2bfsm": {
        "run_id": "pilot_018_assetfix_v009",
        "qualification_evidence": "data/runs/manual_rtl_teacher/pilot_018_assetfix_v009/reports/asset_qualification_evidence.jsonl",
        "qualification_report": "data/runs/manual_rtl_teacher/pilot_018_assetfix_v009/reports/asset_qualification_validation.json",
        "qualification_freeze": "data/runs/manual_rtl_teacher/pilot_018_assetfix_v009/reports/qualification_freeze.json",
    },
}


class PreparationError(ValueError):
    """Raised when immutable preparation inputs do not match."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"required JSON input is missing or symlinked: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"invalid JSON input: {path}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"required JSONL input is missing or symlinked: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PreparationError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise PreparationError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(value)
    return rows


def _write_exclusive(path: Path, value: Any, *, jsonl: bool = False) -> str:
    if path.exists() or path.is_symlink():
        raise PreparationError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if jsonl:
        content = b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            for row in value
        )
    else:
        content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    return hashlib.sha256(content).hexdigest()


def _validate_coverage(coverage_path: Path) -> dict[str, Any]:
    coverage = _load_json(coverage_path)
    if coverage.get("source_commit") != SOURCE_COMMIT:
        raise PreparationError("coverage source commit mismatch")
    if coverage.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise PreparationError("coverage source-tree hash mismatch")
    if coverage.get("frozen_split_sha256") != SPLIT_SHA256:
        raise PreparationError("coverage split hash mismatch")
    if coverage.get("remaining_source_ids") != list(REMAINING_SOURCE_IDS):
        raise PreparationError("coverage remaining-source order mismatch")
    if coverage.get("remaining_train_count") != len(REMAINING_SOURCE_IDS):
        raise PreparationError("coverage remaining-source count mismatch")
    return coverage


def _diagnosis_rows(inventory: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": "Prob149_ece241_2013_q4",
            "task_id": inventory["Prob149_ece241_2013_q4"]["task_id"],
            "split": "train",
            "classification": "testbench_behavior_defect",
            "qualification_status": "positive_fixture_passed_under_wrong_interface_contract",
            "evidence_basis": "The preserved public prompt/interface declares s[3:1] and outputs fr3, fr2, fr1; the prior public-spec testbench and positive fixture used s[2:0] and fr0. The prior qualification result therefore did not bind to the public interface.",
            "action": "correction_retry_required",
            "correction_scope": "replace testbench and both public-spec qualification fixtures with the declared interface; do not alter upstream files",
            "private_content_inspected": False,
        },
        {
            "source_id": "Prob137_fsm_serial",
            "task_id": inventory["Prob137_fsm_serial"]["task_id"],
            "split": "train",
            "classification": "candidate_failure_exhausted",
            "qualification_status": "qualified",
            "evidence_basis": "The asset qualification positive and both negative mutations passed their respective contracts; four candidate attempts then ended in functional_mismatch.",
            "action": "exclude_from_asset_retry",
            "correction_scope": "no asset change and no automatic attempt-5 generation",
            "private_content_inspected": False,
        },
        {
            "source_id": "Prob139_2013_q2bfsm",
            "task_id": inventory["Prob139_2013_q2bfsm"]["task_id"],
            "split": "train",
            "classification": "positive_fixture_defect",
            "qualification_status": "positive_fixture_failed",
            "evidence_basis": "The public specification requires detection of 1,0,1 in three successive cycles. The preserved positive fixture inserts an additional X101 state, so the supplied three-cycle stimulus cannot reach the g assertion state.",
            "action": "correction_retry_required",
            "correction_scope": "replace the positive public-spec fixture only; preserve the standalone checker and negative mutation intent",
            "private_content_inspected": False,
        },
        {
            "source_id": "Prob146_fsm_serialdata",
            "task_id": inventory["Prob146_fsm_serialdata"]["task_id"],
            "split": "train",
            "classification": "candidate_failure_exhausted",
            "qualification_status": "qualified",
            "evidence_basis": "The asset qualification positive and both negative mutations passed their respective contracts; four candidate attempts then ended in functional_mismatch.",
            "action": "exclude_from_asset_retry",
            "correction_scope": "no asset change and no automatic attempt-5 generation",
            "private_content_inspected": False,
        },
    ]


def prepare(
    *,
    inventory_path: Path,
    coverage_path: Path,
    diagnosis_output: Path,
    ids_output: Path,
    selection_output: Path,
    authorization_output: Path,
) -> dict[str, Any]:
    coverage = _validate_coverage(coverage_path)
    inventory_rows = _load_jsonl(inventory_path)
    inventory = {row.get("source_id"): row for row in inventory_rows}
    if set(inventory) < set(REMAINING_SOURCE_IDS):
        raise PreparationError("remaining source IDs are missing from inventory")

    for source_id in SELECTED_SOURCE_IDS:
        row = inventory[source_id]
        if row.get("top_module") != "TopModule":
            raise PreparationError(f"selected source is not a TopModule row: {source_id}")

    parent_bindings: dict[str, dict[str, Any]] = {}
    for source_id, artifacts in PARENT_ARTIFACTS.items():
        binding = {"run_id": artifacts["run_id"]}
        for label in ("qualification_evidence", "qualification_report", "qualification_freeze"):
            path = Path(artifacts[label])
            if not path.is_file() or path.is_symlink():
                raise PreparationError(f"parent artifact missing: {path}")
            binding[f"{label}_path"] = artifacts[label]
            binding[f"{label}_sha256"] = sha256_file(path)
        parent_bindings[source_id] = binding

    diagnosis = {
        "schema_version": "rtl_generation_remaining_train_diagnosis_v0.1",
        "status": "diagnosed_read_only",
        "source_dataset": "VerilogEval",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "inventory_sha256": sha256_file(inventory_path),
        "frozen_split_sha256": SPLIT_SHA256,
        "coverage_report_path": coverage_path.as_posix(),
        "coverage_report_sha256": sha256_file(coverage_path),
        "remaining_source_ids": list(REMAINING_SOURCE_IDS),
        "selected_for_asset_retry": list(SELECTED_SOURCE_IDS),
        "excluded_candidate_exhausted": list(EXCLUDED_CANDIDATE_EXHAUSTED_IDS),
        "parent_bindings": parent_bindings,
        "rows": _diagnosis_rows(inventory),
        "policy": {
            "reference_inspected": False,
            "private_testbench_inspected": False,
            "rtlbench_executed": False,
            "candidate_generation_repeated": False,
            "validation_and_test_splits_untouched": True,
            "promotion_allowed": False,
        },
    }
    diagnosis_hash = _write_exclusive(diagnosis_output, diagnosis)
    if ids_output.exists() or ids_output.is_symlink():
        raise PreparationError(f"refusing to replace existing output: {ids_output}")
    ids_output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ids_output.parent, 0o700)
    ids_bytes = ("\n".join(SELECTED_SOURCE_IDS) + "\n").encode("utf-8")
    with ids_output.open("xb") as handle:
        handle.write(ids_bytes)
    os.chmod(ids_output, 0o600)
    ids_hash = hashlib.sha256(ids_bytes).hexdigest()

    selection_rows = []
    for order, source_id in enumerate(SELECTED_SOURCE_IDS, 1):
        source = inventory[source_id]
        selection_rows.append({
            "selection_order": order,
            "source_id": source_id,
            "task_id": source["task_id"],
            "split": "train",
            "current_readiness": source.get("verification_readiness"),
            "blocking_reason": "public_asset_contract_defect",
            "selection_reason": "bounded retry after read-only public-interface/oracle diagnosis",
            "selection_role": "correction_retry",
            "design_family": source.get("design_family"),
            "source_prompt_sha256": source.get("source_prompt_sha256"),
            "top_module": source.get("top_module"),
        })
    selection = {
        "schema_version": "rtl_verification_asset_correction_selection_v0.2",
        "selection_id": SELECTION_ID,
        "selection_kind": "qualification_retry",
        "batch_id": SELECTION_ID,
        "correction_version": CORRECTION_VERSION,
        "source_dataset": "VerilogEval",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "base_inventory_sha256": INVENTORY_SHA256,
        "base_split_sha256": SPLIT_SHA256,
        "split": "train",
        "selected_count": len(SELECTED_SOURCE_IDS),
        "selection_ids_sha256": ids_hash,
        "diagnosis_report_sha256": diagnosis_hash,
        "parent_run_id": "pilot_017_assetfix_v008_final_train_remainder;pilot_018_assetfix_v009",
        "parent_qualification_report_sha256": ";".join(
            parent_bindings[source_id]["qualification_report_sha256"] for source_id in SELECTED_SOURCE_IDS
        ),
        "parent_qualification_evidence_sha256": ";".join(
            parent_bindings[source_id]["qualification_evidence_sha256"] for source_id in SELECTED_SOURCE_IDS
        ),
        "parent_qualified_subset_binding_sha256": diagnosis_hash,
        "retry_reason": "correct public verification assets after read-only diagnosis",
        "rows": selection_rows,
        "excluded_source_ids": list(EXCLUDED_CANDIDATE_EXHAUSTED_IDS),
        "errors": [],
        "ok": True,
    }
    selection_hash = _write_exclusive(selection_output, selection)
    authorization = {
        "schema_version": "rtl_verification_asset_correction_retry_authorization_v0.1",
        "status": "prepared_pending_isolated_execution_authorization",
        "selection_id": SELECTION_ID,
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "inventory_sha256": INVENTORY_SHA256,
        "frozen_split_sha256": SPLIT_SHA256,
        "selected_source_ids": list(SELECTED_SOURCE_IDS),
        "excluded_candidate_exhausted_ids": list(EXCLUDED_CANDIDATE_EXHAUSTED_IDS),
        "diagnosis_report_sha256": diagnosis_hash,
        "selection_report_sha256": selection_hash,
        "selection_ids_sha256": ids_hash,
        "selection_count": len(SELECTED_SOURCE_IDS),
        "qualification_case_count": len(SELECTED_SOURCE_IDS) * 3,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "rtlbench_executed": False,
        "one_isolated_retry_invocation_authorized": False,
        "next_boundary": "separate operator authorization before isolated qualification",
    }
    authorization_hash = _write_exclusive(authorization_output, authorization)
    return {
        "ok": True,
        "diagnosis_path": diagnosis_output.as_posix(),
        "diagnosis_sha256": diagnosis_hash,
        "ids_path": ids_output.as_posix(),
        "ids_sha256": ids_hash,
        "selection_path": selection_output.as_posix(),
        "selection_sha256": selection_hash,
        "authorization_path": authorization_output.as_posix(),
        "authorization_sha256": authorization_hash,
        "selected_source_ids": list(SELECTED_SOURCE_IDS),
        "excluded_candidate_exhausted_ids": list(EXCLUDED_CANDIDATE_EXHAUSTED_IDS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    parser.add_argument("--diagnosis-output", required=True, type=Path)
    parser.add_argument("--ids-output", required=True, type=Path)
    parser.add_argument("--selection-output", required=True, type=Path)
    parser.add_argument("--authorization-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare(
            inventory_path=args.inventory,
            coverage_path=args.coverage,
            diagnosis_output=args.diagnosis_output,
            ids_output=args.ids_output,
            selection_output=args.selection_output,
            authorization_output=args.authorization_output,
        )
    except (OSError, UnicodeError, PreparationError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
