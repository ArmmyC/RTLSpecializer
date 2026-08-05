"""Deterministic train-only selection for the next RTL correction batch.

This module selects a new bounded batch without changing the v0.1 five-row
smoke controls.  It consumes metadata-only inventory, split, and acquisition
records and emits no source, reference, testbench, or private path content.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from scripts.dataset.rtl_generation_inventory import (
    INVENTORY_SCHEMA_VERSION,
    SPLIT_SCHEMA_VERSION,
    _load_inventory,
    validate_generation_split,
)


SELECTION_SCHEMA_VERSION = "rtl_verification_asset_correction_selection_v0.2"
BATCH_ID = "verilog_eval_generation_v002_batch20_v001"
CORRECTION_VERSION = "assetfix_v003"
SOURCE_DATASET = "VerilogEval"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
BASE_INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
BASE_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"

SMOKE_SOURCE_IDS = (
    "Prob001_zero",
    "Prob020_mt2015_eq2",
    "Prob071_always_casez",
    "Prob048_m2014_q4c",
    "Prob079_fsm3onehot",
)

BATCH20_SOURCE_IDS = (
    "Prob029_m2014_q4g",
    "Prob055_conditional",
    "Prob092_gatesv100",
    "Prob106_always_nolatches",
    "Prob112_always_case2",
    "Prob122_kmap4",
    "Prob060_m2014_q4k",
    "Prob061_2014_q4a",
    "Prob084_ece241_2013_q12",
    "Prob085_shift4",
    "Prob105_rotate100",
    "Prob115_shift18",
    "Prob082_lfsr32",
    "Prob086_lfsr5",
    "Prob100_fsm3comb",
    "Prob107_fsm1s",
    "Prob109_fsm1",
    "Prob110_fsm2",
    "Prob111_fsm2s",
    "Prob133_2014_q3fsm",
)

SELECTION_ROLES = {
    "Prob029_m2014_q4g": "basic_combinational_logic",
    "Prob055_conditional": "arithmetic_comparison",
    "Prob092_gatesv100": "wide_vector_logic",
    "Prob106_always_nolatches": "case_decoder_logic",
    "Prob112_always_case2": "priority_encoder_logic",
    "Prob122_kmap4": "truth_table_logic",
    "Prob060_m2014_q4k": "synchronous_shift_register",
    "Prob061_2014_q4a": "enabled_register_stage",
    "Prob084_ece241_2013_q12": "registered_memory_mux",
    "Prob085_shift4": "asynchronous_reset_shift_register",
    "Prob105_rotate100": "wide_rotator",
    "Prob115_shift18": "wide_arithmetic_shift",
    "Prob082_lfsr32": "wide_feedback_register",
    "Prob086_lfsr5": "feedback_register",
    "Prob100_fsm3comb": "small_fsm_combinational_logic",
    "Prob107_fsm1s": "small_fsm_synchronous_reset",
    "Prob109_fsm1": "small_fsm_asynchronous_reset",
    "Prob110_fsm2": "two_input_fsm_asynchronous_reset",
    "Prob111_fsm2s": "two_input_fsm_synchronous_reset",
    "Prob133_2014_q3fsm": "sequence_sensitive_fsm",
}

DIVERSITY_TAGS = {
    "Prob029_m2014_q4g": ("combinational_logic",),
    "Prob055_conditional": ("arithmetic_comparison",),
    "Prob092_gatesv100": ("width_sensitive", "combinational_logic"),
    "Prob106_always_nolatches": ("priority_case_logic", "combinational_logic"),
    "Prob112_always_case2": ("priority_case_logic", "combinational_logic"),
    "Prob122_kmap4": ("truth_table_logic", "combinational_logic"),
    "Prob060_m2014_q4k": ("register_or_shift", "synchronous_reset"),
    "Prob061_2014_q4a": ("register_or_shift",),
    "Prob084_ece241_2013_q12": ("register_or_shift", "registered_memory"),
    "Prob085_shift4": ("register_or_shift", "asynchronous_reset"),
    "Prob105_rotate100": ("register_or_shift", "width_sensitive"),
    "Prob115_shift18": ("register_or_shift", "width_sensitive"),
    "Prob082_lfsr32": ("register_or_shift", "feedback_register", "synchronous_reset", "width_sensitive"),
    "Prob086_lfsr5": ("register_or_shift", "feedback_register", "synchronous_reset"),
    "Prob100_fsm3comb": ("fsm", "combinational_logic"),
    "Prob107_fsm1s": ("fsm", "synchronous_reset"),
    "Prob109_fsm1": ("fsm", "asynchronous_reset"),
    "Prob110_fsm2": ("fsm", "asynchronous_reset"),
    "Prob111_fsm2s": ("fsm", "synchronous_reset"),
    "Prob133_2014_q3fsm": ("fsm", "sequence_sensitive", "synchronous_reset"),
}

REQUIRED_DIVERSITY_TARGETS = {
    "combinational_logic": 6,
    "arithmetic_comparison": 1,
    "priority_case_logic": 2,
    "width_sensitive": 3,
    "register_or_shift": 8,
    "synchronous_reset": 5,
    "asynchronous_reset": 3,
    "fsm": 6,
    "sequence_sensitive": 1,
}
ADVISORY_DIVERSITY_TARGETS = {"counter_or_timer": 1}
MIN_BOUNDED_BATCH_SIZE = 20
MAX_BOUNDED_BATCH_SIZE = 40


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def load_source_ids(path: Path | None) -> list[str]:
    if path is None:
        return list(BATCH20_SOURCE_IDS)
    if path.is_symlink():
        raise ValueError("source-ID allowlist must not be a symlink")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise ValueError("source-ID allowlist contains duplicates")
    return values


def load_selection_metadata(path: Path | None) -> tuple[dict[str, str], dict[str, tuple[str, ...]], dict[str, int], dict[str, int]]:
    """Load public selection annotations for a non-default batch.

    The metadata file contains either a ``rows`` array with ``source_id``,
    ``selection_role`` and ``diversity_tags`` fields, or an object keyed by
    source ID.  It contains public classification only; no source content or
    private paths are accepted here.
    """
    if path is None:
        return {}, {}, {}, {}
    value = _load_json(path)
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        entries = value["rows"]
        required = value.get("required_targets", {})
        advisory = value.get("advisory_targets", {})
    elif isinstance(value, dict):
        entries = [
            {"source_id": source_id, **metadata}
            for source_id, metadata in value.items()
            if isinstance(metadata, dict)
        ]
        required = {}
        advisory = {}
    else:
        raise ValueError("selection metadata must be an object")
    roles: dict[str, str] = {}
    tags: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("selection metadata rows must be objects")
        source_id = entry.get("source_id")
        role = entry.get("selection_role")
        row_tags = entry.get("diversity_tags")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("selection metadata source_id is invalid")
        if not isinstance(role, str) or not role:
            raise ValueError(f"selection role is missing: {source_id}")
        if not isinstance(row_tags, list) or not row_tags or not all(isinstance(tag, str) and tag for tag in row_tags):
            raise ValueError(f"selection diversity tags are invalid: {source_id}")
        if source_id in roles:
            raise ValueError(f"selection metadata contains duplicate source_id: {source_id}")
        roles[source_id] = role
        tags[source_id] = tuple(row_tags)
    for label, target in (("required_targets", required), ("advisory_targets", advisory)):
        if not isinstance(target, dict) or any(not isinstance(key, str) or not isinstance(value, int) or value < 0 for key, value in target.items()):
            raise ValueError(f"{label} must map category names to non-negative integers")
    return roles, tags, dict(required), dict(advisory)


def _diversity_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.get("diversity_tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items()))


def _validate_acquisition(
    path: Path,
    *,
    expected_source_commit: str,
    expected_source_tree_sha256: str,
) -> list[str]:
    errors: list[str] = []
    try:
        acquisition = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [str(exc)]
    if not isinstance(acquisition, dict):
        return ["source acquisition must be an object"]
    if acquisition.get("source_commit") != expected_source_commit:
        errors.append("source acquisition commit mismatch")
    if acquisition.get("source_tree_sha256") != expected_source_tree_sha256:
        errors.append("source acquisition tree hash mismatch")
    if acquisition.get("repository") != "NVlabs/verilog-eval":
        errors.append("source acquisition repository mismatch")
    if acquisition.get("dirty_checkout") is not False:
        errors.append("source checkout is not recorded clean")
    if acquisition.get("errors") not in ([], None):
        errors.append("source acquisition contains errors")
    if acquisition.get("row_count") != 156:
        errors.append("source acquisition row count mismatch")
    return errors


def select_batch_rows(
    inventory_path: Path,
    split_path: Path,
    acquisition_path: Path,
    ids_output: Path,
    report_output: Path,
    *,
    source_ids: Iterable[str] | None = None,
    expected_source_commit: str = SOURCE_COMMIT,
    expected_source_tree_sha256: str = SOURCE_TREE_SHA256,
    expected_inventory_sha256: str = BASE_INVENTORY_SHA256,
    expected_split_sha256: str = BASE_SPLIT_SHA256,
    correction_version: str = CORRECTION_VERSION,
    batch_id: str = BATCH_ID,
    expected_count: int = 20,
    selection_roles: dict[str, str] | None = None,
    diversity_tags: dict[str, tuple[str, ...]] | None = None,
    required_targets: dict[str, int] | None = None,
    advisory_targets: dict[str, int] | None = None,
    excluded_source_ids: Iterable[str] | None = None,
    enforce_bounded_size: bool = False,
    selection_metadata_sha256: str | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    requested = list(source_ids) if source_ids is not None else list(BATCH20_SOURCE_IDS)
    is_default_batch = requested == list(BATCH20_SOURCE_IDS)
    selection_roles = selection_roles if selection_roles is not None else (SELECTION_ROLES if is_default_batch else {})
    diversity_tags = diversity_tags if diversity_tags is not None else (DIVERSITY_TAGS if is_default_batch else {})
    required_targets = required_targets if required_targets is not None else (REQUIRED_DIVERSITY_TARGETS if is_default_batch else {})
    advisory_targets = advisory_targets if advisory_targets is not None else (ADVISORY_DIVERSITY_TARGETS if is_default_batch else {})
    excluded_order = list(excluded_source_ids) if excluded_source_ids is not None else list(SMOKE_SOURCE_IDS)
    excluded_ids = set(excluded_order)
    if enforce_bounded_size and not MIN_BOUNDED_BATCH_SIZE <= expected_count <= MAX_BOUNDED_BATCH_SIZE:
        errors.append(f"bounded selection count must be between {MIN_BOUNDED_BATCH_SIZE} and {MAX_BOUNDED_BATCH_SIZE}")
    if len(requested) != expected_count:
        errors.append(f"selection requires exactly {expected_count} source IDs")
    if len(requested) != len(set(requested)):
        errors.append("selection contains duplicate source IDs")
    if not requested:
        errors.append("selection is empty")
    if ids_output.exists() or ids_output.is_symlink():
        errors.append("IDs output already exists")
    if report_output.exists() or report_output.is_symlink():
        errors.append("selection report output already exists")

    actual_inventory_sha256: str | None = None
    actual_split_sha256: str | None = None
    inventory: list[dict[str, Any]] = []
    split: dict[str, Any] = {}
    try:
        actual_inventory_sha256 = sha256_file(inventory_path)
        actual_split_sha256 = sha256_file(split_path)
        inventory = _load_inventory(inventory_path)
        split = _load_json(split_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))

    errors.extend(
        _validate_acquisition(
            acquisition_path,
            expected_source_commit=expected_source_commit,
            expected_source_tree_sha256=expected_source_tree_sha256,
        )
    )
    if actual_inventory_sha256 != expected_inventory_sha256:
        errors.append("base inventory hash mismatch")
    if actual_split_sha256 != expected_split_sha256:
        errors.append("frozen split hash mismatch")

    if inventory:
        split_report, split_code = validate_generation_split(inventory_path, split_path, expected_seed=7)
        if split_code:
            errors.extend(split_report.get("errors", []))
    if not isinstance(split, dict) or split.get("schema_version") != SPLIT_SCHEMA_VERSION:
        errors.append("invalid split manifest")

    by_id = {row.get("source_id"): row for row in inventory}
    train_ids = set(split.get("splits", {}).get("train", [])) if isinstance(split, dict) else set()
    smoke_ids = excluded_ids
    selected_rows: list[dict[str, Any]] = []

    if set(requested) != set(selection_roles) or set(requested) != set(diversity_tags):
        errors.append("selection metadata is incomplete")

    for source_id in requested:
        row = by_id.get(source_id)
        if row is None:
            errors.append(f"source ID missing from inventory: {source_id}")
            continue
        if source_id not in train_ids:
            errors.append(f"source ID is outside train split: {source_id}")
        if source_id in smoke_ids:
            errors.append(f"source ID is in the excluded prior-batch set: {source_id}")
        if row.get("source_commit") != expected_source_commit:
            errors.append(f"source commit mismatch: {source_id}")
        if row.get("verification_readiness") != "needs_testbench":
            errors.append(f"source is not currently needs_testbench: {source_id}")
        if not any("reference material" in str(reason) for reason in row.get("readiness_reasons", [])):
            errors.append(f"source is not blocked by a reference-only dependency: {source_id}")
        if row.get("interface_deterministic") is not True:
            errors.append(f"source interface is not deterministic: {source_id}")
        tags = list(diversity_tags.get(source_id, ()))
        if not tags:
            errors.append(f"source has no public-spec diversity tags: {source_id}")
        selected_rows.append({
            "selection_order": len(selected_rows) + 1,
            "source_id": source_id,
            "task_id": row.get("task_id"),
            "split": "train",
            "current_readiness": row.get("verification_readiness"),
            "blocking_reason": "reference_only_dependency",
            "selection_reason": "bounded public-spec diversity correction batch",
            "selection_role": selection_roles.get(source_id, ""),
            "design_family": row.get("design_family"),
            "behavior_categories": row.get("behavior_categories", []),
            "diversity_tags": tags,
            "source_prompt_sha256": row.get("source_prompt_sha256"),
            "top_module": row.get("top_module"),
            "interface_deterministic": row.get("interface_deterministic"),
            "clock_signal_count": row.get("clock_signal_count"),
            "reset_signal_count": row.get("reset_signal_count"),
        })

    achieved = _diversity_counts(selected_rows)
    targets = {
        "required": required_targets,
        "advisory": advisory_targets,
    }
    unmet: list[str] = []
    for category, minimum in required_targets.items():
        if achieved.get(category, 0) < minimum:
            unmet.append(category)
    for category, minimum in advisory_targets.items():
        if achieved.get(category, 0) < minimum:
            unmet.append(category)
    if any(category in unmet for category in required_targets):
        errors.append("required diversity targets are not met")

    report = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "source_dataset": SOURCE_DATASET,
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "source_acquisition_sha256": sha256_file(acquisition_path) if acquisition_path.is_file() else None,
        "base_inventory_sha256": actual_inventory_sha256,
        "base_split_sha256": actual_split_sha256,
        "correction_version": correction_version,
        "batch_id": batch_id,
        "split": "train",
        "excluded_source_ids": excluded_order,
        "excluded_source_ids_sha256": hashlib.sha256(
            "".join(f"{source_id}\n" for source_id in excluded_order).encode("utf-8")
        ).hexdigest(),
        "selection_metadata_sha256": selection_metadata_sha256,
        "requested_count": expected_count,
        "selected_count": len(selected_rows),
        "selection_algorithm": "explicit_public_spec_diversity_allowlist_v1",
        "classification_basis": "public_specification_and_interface_metadata",
        "diversity_targets": targets,
        "diversity_achieved": achieved,
        "unmet_diversity_targets": sorted(unmet),
        "rows": selected_rows,
        "errors": sorted(set(errors)),
    }
    if errors or len(selected_rows) != expected_count:
        return {**report, "ok": False}, 1

    ids_content = "".join(f"{source_id}\n" for source_id in requested).encode("utf-8")
    report["selection_ids_sha256"] = hashlib.sha256(ids_content).hexdigest()
    _write_exclusive(ids_output, ids_content)
    _write_exclusive(
        report_output,
        (json.dumps({**report, "ok": True}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        **report,
        "ok": True,
        "ids_output": ids_output.as_posix(),
        "report_output": report_output.as_posix(),
    }, 0


__all__ = [
    "ADVISORY_DIVERSITY_TARGETS",
    "BATCH20_SOURCE_IDS",
    "BATCH_ID",
    "BASE_INVENTORY_SHA256",
    "BASE_SPLIT_SHA256",
    "CORRECTION_VERSION",
    "DIVERSITY_TAGS",
    "MAX_BOUNDED_BATCH_SIZE",
    "MIN_BOUNDED_BATCH_SIZE",
    "REQUIRED_DIVERSITY_TARGETS",
    "SELECTION_SCHEMA_VERSION",
    "SMOKE_SOURCE_IDS",
    "SOURCE_COMMIT",
    "SOURCE_TREE_SHA256",
    "load_source_ids",
    "load_selection_metadata",
    "select_batch_rows",
    "sha256_file",
]
