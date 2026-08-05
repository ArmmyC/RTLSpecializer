"""Static controls for a bounded verification correction overlay.

This module records only authored testbench metadata and hashes.  It never
reads reference or original testbench bytes, never executes RTL, and never
marks a row executable-ready before an independent qualification run.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from scripts.dataset.rtl_generation_batch_selection import (
    BATCH20_SOURCE_IDS,
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    CORRECTION_VERSION,
    SELECTION_SCHEMA_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
    sha256_file,
)


MANIFEST_SCHEMA_VERSION = "rtl_verification_asset_correction_v0.2"
ROW_SCHEMA_VERSION = "rtl_verification_asset_correction_row_v0.2"
MUTATION_SCHEMA_VERSION = "rtl_correction_mutation_contract_v0.1"
STATIC_REPORT_SCHEMA_VERSION = "rtl_verification_asset_correction_static_validation_v0.1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)
_TOP_INSTANCE_RE = re.compile(r"\bTopModule\s+[A-Za-z_][A-Za-z0-9_$]*\s*\(", re.IGNORECASE)
_RESULT_RE = re.compile(r"(?i)Mismatches\s*:\s*%[0-9]*d\b")

PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
    "refmodule",
)
NONDETERMINISTIC_MARKERS = (
    "$random",
    "$urandom",
    "$time",
    "$realtime",
    "$fopen",
    "$readmem",
    "$writemem",
    "$system",
)
FORBIDDEN_DEPENDENCY_MARKERS = (
    "`include",
    "import ",
    "package ",
    "interface ",
    "bind ",
)

MUTATION_NAMES = {
    "Prob004_vector2": ("constant_zero", "wrong_byte_order"),
    "Prob006_vectorr": ("constant_zero", "wrong_bit_order"),
    "Prob010_mt2015_q4a": ("constant_zero", "wrong_boolean_expression"),
    "Prob015_vector1": ("constant_zero", "swapped_halves"),
    "Prob026_alwaysblock1": ("constant_zero", "always_output_mismatch"),
    "Prob036_ringer": ("constant_zero", "wrong_vibrate_selection"),
    "Prob042_vector4": ("constant_zero", "zero_extend"),
    "Prob051_gates4": ("constant_zero", "wrong_xor"),
    "Prob064_vector3": ("constant_zero", "wrong_concat_tail"),
    "Prob069_truthtable1": ("constant_zero", "wrong_truth_table"),
    "Prob070_ece241_2013_q2": ("constant_zero", "wrong_boolean_form"),
    "Prob087_gates": ("constant_zero", "wrong_nand"),
    "Prob045_edgedetect2": ("constant_zero", "same_cycle_edge"),
    "Prob049_m2014_q4b": ("synchronous_reset", "wrong_data_capture"),
    "Prob054_edgedetect": ("constant_zero", "same_cycle_pedge"),
    "Prob058_alwaysblock2": ("constant_zero", "no_ff_delay"),
    "Prob074_ece241_2014_q4": ("constant_zero", "wrong_gate_feedback"),
    "Prob088_ece241_2014_q5b": ("synchronous_reset", "wrong_mealy_output"),
    "Prob095_review2015_fsmshift": ("constant_zero", "three_cycles_only"),
    "Prob096_review2015_fsmseq": ("constant_zero", "clear_on_nonmatch"),
    "Prob029_m2014_q4g": ("constant_zero", "inverted_output"),
    "Prob055_conditional": ("constant_zero", "wrong_min_operand"),
    "Prob092_gatesv100": ("constant_zero", "wrong_neighbor_direction"),
    "Prob106_always_nolatches": ("constant_zero", "wrong_scancode_mapping"),
    "Prob112_always_case2": ("constant_zero", "highest_bit_priority"),
    "Prob122_kmap4": ("constant_zero", "inverted_output"),
    "Prob060_m2014_q4k": ("constant_zero", "one_cycle_latency"),
    "Prob061_2014_q4a": ("hold_when_enabled", "load_priority_removed"),
    "Prob084_ece241_2013_q12": ("constant_zero", "wrong_mux_address"),
    "Prob085_shift4": ("missing_reset", "load_priority_removed"),
    "Prob105_rotate100": ("constant_zero", "wrong_rotation_direction"),
    "Prob115_shift18": ("logical_right_shift", "wrong_shift_amount"),
    "Prob082_lfsr32": ("constant_zero", "wrong_feedback_tap"),
    "Prob086_lfsr5": ("constant_zero", "wrong_feedback_tap"),
    "Prob100_fsm3comb": ("constant_zero", "wrong_fsm_transition"),
    "Prob107_fsm1s": ("missing_reset", "wrong_fsm_transition"),
    "Prob109_fsm1": ("missing_reset", "wrong_fsm_transition"),
    "Prob110_fsm2": ("missing_reset", "wrong_fsm_transition"),
    "Prob111_fsm2s": ("missing_reset", "wrong_fsm_transition"),
    "Prob133_2014_q3fsm": ("missing_reset", "wrong_sequence_count"),
}


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {line_number} is not an object")
        rows.append(value)
    return rows


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _check_file(path: Path) -> tuple[bytes | None, list[str]]:
    errors: list[str] = []
    try:
        metadata = path.lstat()
    except OSError as exc:
        return None, [f"could not stat correction file: {exc}"]
    if stat.S_ISLNK(metadata.st_mode):
        errors.append("correction file is a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        errors.append("correction file is not regular")
    if metadata.st_nlink != 1:
        errors.append("correction file is hard-linked")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        errors.append("correction file must have mode 0600")
    if errors:
        return None, errors
    try:
        return path.read_bytes(), []
    except OSError as exc:
        return None, [f"could not read correction file: {exc}"]


def static_testbench_audit(content: bytes) -> tuple[dict[str, Any], list[str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {}, ["testbench is not valid UTF-8"]
    lowered = text.casefold()
    errors: list[str] = []
    for marker in PRIVATE_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"private marker found: {marker}")
    for marker in NONDETERMINISTIC_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"nondeterministic marker found: {marker}")
    for marker in FORBIDDEN_DEPENDENCY_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"forbidden dependency marker found: {marker}")
    modules = _MODULE_RE.findall(text)
    if modules.count("tb") != 1:
        errors.append("testbench must declare exactly one tb module")
    if len(modules) != 1:
        errors.append("testbench must not declare additional modules")
    candidate_instances = len(_TOP_INSTANCE_RE.findall(text))
    if candidate_instances != 1:
        errors.append("testbench must instantiate TopModule exactly once")
    if not _RESULT_RE.search(text):
        errors.append("testbench lacks the canonical mismatch result format")
    if len(re.findall(r"\$finish\b", text, flags=re.IGNORECASE)) != 1:
        errors.append("testbench must have exactly one final finish")
    audit = {
        "candidate_instantiation_count": candidate_instances,
        "canonical_result_format_found": bool(_RESULT_RE.search(text)),
        "module_declarations": modules,
        "module_declaration_count": len(modules),
        "nondeterministic_marker_found": any(marker.casefold() in lowered for marker in NONDETERMINISTIC_MARKERS),
        "private_marker_found": any(marker.casefold() in lowered for marker in PRIVATE_MARKERS),
        "support_file_count": 0,
    }
    return audit, sorted(set(errors))


def _mutation_contracts(source_id: str) -> list[dict[str, Any]]:
    names = MUTATION_NAMES.get(source_id, ("constant_zero", "incorrect_behavior"))
    contracts = [{
        "expected_outcome": "accepted",
        "execution_status": "pending_isolated_qualification",
        "kind": "positive",
        "name": "public_spec_candidate",
        "oracle_basis": "public_specification_only",
        "schema_version": MUTATION_SCHEMA_VERSION,
    }]
    contracts.extend({
        "expected_outcome": "rejected",
        "execution_status": "pending_isolated_qualification",
        "kind": "negative",
        "name": name,
        "oracle_basis": "public_specification_only",
        "schema_version": MUTATION_SCHEMA_VERSION,
    } for name in names)
    return contracts


def _source_map(inventory_path: Path) -> dict[str, dict[str, Any]]:
    return {row["source_id"]: row for row in _load_jsonl(inventory_path)}


def _selection_ids(selection: dict[str, Any], ids_path: Path) -> list[str]:
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = selection.get("rows")
    if not isinstance(rows, list):
        raise ValueError("selection rows must be an array")
    row_ids = [row.get("source_id") for row in rows if isinstance(row, dict)]
    if ids != row_ids:
        raise ValueError("selection IDs and selection report order differ")
    return ids


def create_batch_manifest(
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
    correction_root: Path,
    output_path: Path,
    *,
    expected_source_commit: str = SOURCE_COMMIT,
    expected_source_tree_sha256: str = SOURCE_TREE_SHA256,
    expected_inventory_sha256: str = BASE_INVENTORY_SHA256,
    expected_split_sha256: str = BASE_SPLIT_SHA256,
    correction_version: str = CORRECTION_VERSION,
    expected_source_ids: list[str] | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    try:
        inventory = _source_map(inventory_path)
        selection = _load_json(selection_path)
        ids = _selection_ids(selection, ids_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    if output_path.exists() or output_path.is_symlink():
        errors.append("manifest output already exists")
    if sha256_file(inventory_path) != expected_inventory_sha256:
        errors.append("base inventory hash mismatch")
    if sha256_file(ids_path) != selection.get("selection_ids_sha256"):
        errors.append("selection ID hash mismatch")
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION:
        errors.append("selection schema mismatch")
    if selection.get("source_commit") != expected_source_commit:
        errors.append("selection source commit mismatch")
    if selection.get("source_tree_sha256") != expected_source_tree_sha256:
        errors.append("selection source tree hash mismatch")
    if selection.get("base_inventory_sha256") != expected_inventory_sha256:
        errors.append("selection inventory binding mismatch")
    if selection.get("base_split_sha256") != expected_split_sha256:
        errors.append("selection split binding mismatch")
    if selection.get("ok") is not True or selection.get("split") != "train":
        errors.append("selection report is not a successful train-only selection")
    if selection.get("correction_version") != correction_version:
        errors.append("selection correction version mismatch")
    pinned_source_ids = expected_source_ids if expected_source_ids is not None else list(BATCH20_SOURCE_IDS)
    if ids != pinned_source_ids:
        errors.append("selection IDs do not match the expected correction order")
    if len(ids) < 20 or len(ids) > 40:
        errors.append("selection is outside the bounded 20-40 task range")
    if selection.get("selected_count") != len(ids):
        errors.append("selection report count does not match the selected task count")
    rows: list[dict[str, Any]] = []
    static_errors: dict[str, list[str]] = {}
    for source_id in ids:
        source = inventory.get(source_id)
        if source is None:
            errors.append(f"source ID missing from inventory: {source_id}")
            continue
        path = correction_root / "tasks" / source_id / "testbench.sv"
        content, file_errors = _check_file(path)
        audit: dict[str, Any] = {}
        if file_errors:
            static_errors[source_id] = file_errors
            errors.extend(f"{source_id}: {item}" for item in file_errors)
        elif content is not None:
            audit, audit_errors = static_testbench_audit(content)
            if audit_errors:
                static_errors[source_id] = audit_errors
                errors.extend(f"{source_id}: {item}" for item in audit_errors)
        corrected_hash = sha256_file(path) if content is not None else None
        row = {
            "authoring_method": "trusted_manual_public_spec",
            "correction_reason": "replace reference-only checker with public-spec-derived standalone testbench",
            "correction_version": correction_version,
            "corrected_testbench_sha256": corrected_hash,
            "dependency_closure": "passed" if not file_errors and not static_errors.get(source_id) else "failed",
            "design_family": source.get("design_family"),
            "frozen_split_sha256": expected_split_sha256,
            "mutation_contracts": _mutation_contracts(source_id),
            "original_prompt_sha256": source.get("source_prompt_sha256"),
            "original_reference_rtl_sha256": source.get("reference_rtl_sha256"),
            "original_testbench_sha256": source.get("testbench_sha256"),
            "qualification_status": "pending_isolated_qualification",
            "reference_copied_to_support": False,
            "reference_modified": False,
            "schema_version": ROW_SCHEMA_VERSION,
            "source_dataset": source.get("source_dataset"),
            "source_id": source_id,
            "source_tree_sha256": expected_source_tree_sha256,
            "split": "train",
            "support_files": [],
            "task_id": source.get("task_id"),
            "testbench_path": f"tasks/{source_id}/testbench.sv",
            "top_module": source.get("top_module"),
            "upstream_commit": expected_source_commit,
            "verification_readiness": "pending_qualification",
            "static_audit": audit,
        }
        if correction_version == "assetfix_v004":
            row.update({
                "public_specification_sha256": source.get("source_prompt_sha256"),
                "selection_ids_sha256": sha256_file(ids_path),
                "selection_report_sha256": sha256_file(selection_path),
            })
        rows.append(row)
    if len(rows) != len(ids):
        errors.append(f"correction manifest must contain exactly {len(ids)} rows")
    manifest_metadata = {
        "base_inventory_sha256": expected_inventory_sha256,
        "base_split_sha256": expected_split_sha256,
        "correction_version": correction_version,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "rows": rows,
        "selection_ids_sha256": sha256_file(ids_path),
        "selection_report_sha256": sha256_file(selection_path),
        "source_commit": expected_source_commit,
        "source_dataset": "VerilogEval",
        "source_tree_sha256": expected_source_tree_sha256,
        "static_dependency_closure_passed": not errors,
    }
    if errors:
        return {**manifest_metadata, "ok": False, "errors": sorted(set(errors)), "static_errors": static_errors}, 1
    content = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_exclusive(output_path, content)
    result = {
        **manifest_metadata,
        "manifest_sha256": sha256_file(output_path),
        "ok": True,
        "output": output_path.as_posix(),
        "static_errors": {},
    }
    return result, 0


__all__ = [
    "BATCH20_SOURCE_IDS",
    "MANIFEST_SCHEMA_VERSION",
    "MUTATION_NAMES",
    "ROW_SCHEMA_VERSION",
    "create_batch_manifest",
    "sha256_file",
    "static_testbench_audit",
]
