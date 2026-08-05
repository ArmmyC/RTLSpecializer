"""Freeze a passed v004 qualification retry and export its public task packet.

This module is a metadata/control-plane boundary.  It combines the preserved
18-task qualification with the passed two-task retry, writes append-only
qualification lists and a hash-bound lineage record, and exports the public
normalization packet plus private verification assets.  It never calls a
model, executes RTLBench, or invokes a compiler, simulator, Docker, or EDA
tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable

from scripts.dataset.data_workspace_layout import initialize_manual_rtl_run
from scripts.dataset.rtl_generation_preparation import (
    export_generation_normalization_batches,
)


SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
BASE_CORRECTION_VERSION = "assetfix_v004"
RETRY_CORRECTION_VERSION = "assetfix_v004_retry_01"
CORRECTION_ROW_SCHEMA_VERSION = "rtl_verification_asset_correction_row_v0.1"
QUALIFICATION_BINDING_SCHEMA_VERSION = "rtl_generation_combined_qualification_binding_v0.1"
FREEZE_SCHEMA_VERSION = "rtl_asset_qualification_frozen_lists_v0.1"
NORMALIZATION_BINDING_SCHEMA_VERSION = "rtl_generation_qualified_normalization_binding_v0.1"
NORMALIZATION_ATTESTATION_SCHEMA_VERSION = "rtl_generation_qualified_normalization_attestation_v0.1"
GENERATION_BATCH_SCHEMA_VERSION = "rtl_generation_normalization_batch_v0.1"
VERIFICATION_ASSET_SCHEMA_VERSION = "rtl_verification_asset_v0.1"

EXPECTED_SELECTION_IDS = (
    "Prob004_vector2",
    "Prob006_vectorr",
    "Prob010_mt2015_q4a",
    "Prob015_vector1",
    "Prob026_alwaysblock1",
    "Prob036_ringer",
    "Prob042_vector4",
    "Prob051_gates4",
    "Prob064_vector3",
    "Prob069_truthtable1",
    "Prob070_ece241_2013_q2",
    "Prob087_gates",
    "Prob045_edgedetect2",
    "Prob049_m2014_q4b",
    "Prob054_edgedetect",
    "Prob058_alwaysblock2",
    "Prob074_ece241_2014_q4",
    "Prob088_ece241_2014_q5b",
    "Prob095_review2015_fsmshift",
    "Prob096_review2015_fsmseq",
)

PUBLIC_PACKET_FIELDS = {
    "task_id",
    "source_id",
    "source_dataset",
    "design_family",
    "license",
    "provenance",
    "raw_specification",
    "deterministic_top_module_hint",
    "deterministic_interface_hints",
    "deterministic_clock_hints",
    "deterministic_reset_hints",
    "normalization_warnings",
}


class Qualified20NormalizationError(ValueError):
    """Raised when a qualification lineage or packet export is unproven."""


def sha256_file(path: Path) -> str:
    _require_regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise Qualified20NormalizationError(f"required path is not a regular file: {path}")


def _require_directory(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise Qualified20NormalizationError(f"required path is not a directory: {path}")


def _read_json(path: Path) -> dict[str, Any]:
    _require_regular(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Qualified20NormalizationError(f"invalid JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise Qualified20NormalizationError(f"JSON input is not an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    _require_regular(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Qualified20NormalizationError(f"could not read JSONL input: {path}") from exc
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Qualified20NormalizationError(f"invalid JSONL row {path}:{index}") from exc
        if not isinstance(value, dict):
            raise Qualified20NormalizationError(f"JSONL row is not an object: {path}:{index}")
        rows.append(value)
    return rows


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise Qualified20NormalizationError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _write_or_validate(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Resume only when an existing derived artifact is byte-identical."""

    if path.exists() or path.is_symlink():
        _require_regular(path)
        if path.read_bytes() != content:
            raise Qualified20NormalizationError(f"existing derived output differs: {path}")
        return
    _write_exclusive(path, content, mode=mode)


def _write_json(path: Path, value: Any) -> None:
    _write_exclusive(path, _json_bytes(value, pretty=True))


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _write_exclusive(path, b"".join(_json_bytes(row) for row in rows))


def _read_ids(path: Path, label: str) -> list[str]:
    _require_regular(path)
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise Qualified20NormalizationError(f"{label} contains duplicate IDs")
    return values


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise Qualified20NormalizationError(f"{label} is not a SHA-256 value")
    try:
        int(value, 16)
    except ValueError as exc:
        raise Qualified20NormalizationError(f"{label} is not a SHA-256 value") from exc
    return value


def _same_order(actual: list[str], expected: tuple[str, ...] | list[str], label: str) -> None:
    if actual != list(expected):
        raise Qualified20NormalizationError(f"{label} order or membership mismatch")


def _report_rows(report: dict[str, Any], expected_ids: list[str], label: str) -> list[dict[str, Any]]:
    rows = report.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise Qualified20NormalizationError(f"{label} rows are invalid")
    if [row.get("source_id") for row in rows] != expected_ids:
        raise Qualified20NormalizationError(f"{label} does not preserve pinned source order")
    if len({row.get("source_id") for row in rows}) != len(rows):
        raise Qualified20NormalizationError(f"{label} contains duplicate source IDs")
    if len({row.get("task_id") for row in rows}) != len(rows):
        raise Qualified20NormalizationError(f"{label} contains duplicate task IDs")
    return rows


def freeze_retry_qualification_lists(
    *,
    retry_run_root: Path,
    validation_report_path: Path,
) -> dict[str, Any]:
    """Freeze retry source-ID lists without replacing any existing output."""

    report = _read_json(validation_report_path)
    if report.get("schema_version") != "rtl_asset_qualification_report_v0.1":
        raise Qualified20NormalizationError("retry qualification report schema mismatch")
    selected = report.get("selected_tasks")
    if selected != 2:
        raise Qualified20NormalizationError("retry qualification report must cover exactly two tasks")
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise Qualified20NormalizationError("retry qualification report rows are invalid")
    source_ids = [row.get("source_id") for row in rows]
    if not all(isinstance(value, str) and value for value in source_ids):
        raise Qualified20NormalizationError("retry qualification report has invalid source IDs")
    if len(source_ids) != len(set(source_ids)):
        raise Qualified20NormalizationError("retry qualification report has duplicate source IDs")
    qualified = [row["source_id"] for row in rows if row.get("qualification_passed") is True]
    failed = [row["source_id"] for row in rows if row.get("qualification_passed") is not True]
    reports_root = retry_run_root / "reports"
    qualified_path = reports_root / "qualified_task_ids.txt"
    failed_path = reports_root / "failed_or_inconclusive_task_ids.txt"
    qualified_bytes = ("\n".join(qualified) + "\n").encode("utf-8")
    failed_bytes = (("\n".join(failed) + "\n") if failed else "").encode("utf-8")
    _write_or_validate(qualified_path, qualified_bytes)
    _write_or_validate(failed_path, failed_bytes)

    root_qualified = retry_run_root / "qualified_task_ids.txt"
    if root_qualified.exists():
        if _read_ids(root_qualified, "existing qualified task IDs") != qualified:
            raise Qualified20NormalizationError("validator-produced qualified list disagrees with report")

    return {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "run_id": retry_run_root.name,
        "qualification_report_sha256": sha256_file(validation_report_path),
        "qualified_task_ids_sha256": sha256_file(qualified_path),
        "failed_or_inconclusive_task_ids_sha256": sha256_file(failed_path),
        "qualified_source_ids": qualified,
        "failed_or_inconclusive_source_ids": failed,
        "qualified_task_ids_identity": "source_id",
        "errors": [],
    }


def _preflight_metadata(run_root: Path) -> dict[str, Any]:
    return _read_json(run_root / "reports" / "asset_qualification_preflight.json")


def _validate_retry_preflight(retry_run_root: Path, report: dict[str, Any]) -> dict[str, Any]:
    preflight = _preflight_metadata(retry_run_root)
    expected = {
        "candidate_manifest_sha256": "fb6f7a4d388b736b29d2c145215250c9dc9a6a57bd0de543d470c68c65a471d9",
        "workspace_tree_sha256": "698e90c3843dbcbb5110efa87a5f212ccab5e118e874fe654dde16e015f5c1ef",
        "case_count": 6,
        "positive_case_count": 2,
        "negative_case_count": 4,
    }
    for key, value in expected.items():
        if preflight.get(key) != value:
            raise Qualified20NormalizationError(f"retry preflight mismatch: {key}")
    if preflight.get("reference_rtl_supplied") is not False or preflight.get("support_file_count") != 0:
        raise Qualified20NormalizationError("retry preflight privacy contract failed")
    if report.get("qualification_passed") is not True:
        raise Qualified20NormalizationError("retry qualification is not passed")
    return preflight


def _check_correction_row(row: dict[str, Any], correction_root: Path, expected_version: str) -> None:
    required = {
        "schema_version", "source_dataset", "source_id", "task_id", "split",
        "design_family", "top_module", "upstream_commit", "original_prompt_sha256",
        "original_reference_rtl_sha256", "original_testbench_sha256",
        "corrected_testbench_sha256", "correction_version", "correction_reason",
        "authoring_method", "reference_modified", "reference_copied_to_support",
        "testbench_path", "support_files", "dependency_closure",
        "verification_readiness", "negative_mutation_validation",
    }
    if set(row) != required:
        raise Qualified20NormalizationError("correction manifest row schema is not exact")
    if row.get("schema_version") != CORRECTION_ROW_SCHEMA_VERSION:
        raise Qualified20NormalizationError("correction manifest row schema mismatch")
    if row.get("source_dataset") != "VerilogEval" or row.get("split") != "train":
        raise Qualified20NormalizationError(f"correction row is not train-only: {row.get('source_id')}")
    if row.get("upstream_commit") != SOURCE_COMMIT or row.get("correction_version") != expected_version:
        raise Qualified20NormalizationError(f"correction row lineage mismatch: {row.get('source_id')}")
    if row.get("support_files") != [] or row.get("reference_modified") is not False or row.get("reference_copied_to_support") is not False:
        raise Qualified20NormalizationError(f"correction row privacy contract failed: {row.get('source_id')}")
    if row.get("dependency_closure") != "passed" or row.get("verification_readiness") != "executable_ready":
        raise Qualified20NormalizationError(f"correction row is not executable-ready: {row.get('source_id')}")
    path_value = row.get("testbench_path")
    if not isinstance(path_value, str) or path_value.startswith("/") or "\\" in path_value or any(part in {"", ".", ".."} for part in path_value.split("/")):
        raise Qualified20NormalizationError(f"unsafe correction testbench path: {row.get('source_id')}")
    path = correction_root.joinpath(*path_value.split("/"))
    _require_regular(path)
    if sha256_file(path) != row.get("corrected_testbench_sha256"):
        raise Qualified20NormalizationError(f"corrected testbench hash mismatch: {row.get('source_id')}")


def _check_retry_mutation_row(
    row: dict[str, Any],
    retry_root: Path,
    base_row: dict[str, Any],
) -> None:
    required = {
        "authoring_method", "base_corrected_testbench_sha256",
        "base_correction_manifest_sha256", "base_correction_version",
        "classification", "correction_reason", "correction_version",
        "dependency_closure", "frozen_split_sha256",
        "original_mutation_candidate_sha256", "original_mutation_name",
        "original_testbench_sha256", "public_specification_sha256",
        "qualification_status", "reference_supplied", "replacement_mutation_name",
        "replacement_mutation_path", "replacement_mutation_sha256",
        "schema_version", "selection_ids_sha256", "selection_report_sha256",
        "source_commit", "source_dataset", "source_id", "source_tree_sha256",
        "split", "static_audit", "support_files", "task_id", "testbench_reused",
        "top_module", "verification_readiness",
    }
    if set(row) != required:
        raise Qualified20NormalizationError(f"retry mutation row schema is not exact: {row.get('source_id')}")
    if row.get("schema_version") != "rtl_verification_asset_correction_retry_v0.1":
        raise Qualified20NormalizationError("retry mutation row schema mismatch")
    if row.get("source_dataset") != "VerilogEval" or row.get("split") != "train":
        raise Qualified20NormalizationError(f"retry mutation is not train-only: {row.get('source_id')}")
    if row.get("source_commit") != SOURCE_COMMIT or row.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise Qualified20NormalizationError(f"retry mutation source lineage mismatch: {row.get('source_id')}")
    if row.get("frozen_split_sha256") != FROZEN_SPLIT_SHA256:
        raise Qualified20NormalizationError(f"retry mutation split lineage mismatch: {row.get('source_id')}")
    if row.get("correction_version") != RETRY_CORRECTION_VERSION or row.get("base_correction_version") != BASE_CORRECTION_VERSION:
        raise Qualified20NormalizationError(f"retry mutation correction version mismatch: {row.get('source_id')}")
    if row.get("task_id") != base_row.get("task_id") or row.get("source_id") != base_row.get("source_id"):
        raise Qualified20NormalizationError(f"retry mutation identity mismatch: {row.get('source_id')}")
    if row.get("reference_supplied") is not False or row.get("support_files") != [] or row.get("testbench_reused") is not True:
        raise Qualified20NormalizationError(f"retry mutation privacy/reuse contract failed: {row.get('source_id')}")
    if row.get("base_corrected_testbench_sha256") != base_row.get("corrected_testbench_sha256"):
        raise Qualified20NormalizationError(f"retry mutation is not bound to the base testbench: {row.get('source_id')}")
    replacement = row.get("replacement_mutation_path")
    if not isinstance(replacement, str) or replacement.startswith("/") or "\\" in replacement or any(part in {"", ".", ".."} for part in replacement.split("/")):
        raise Qualified20NormalizationError(f"unsafe retry mutation path: {row.get('source_id')}")
    replacement_path = retry_root.joinpath(*replacement.split("/"))
    _require_regular(replacement_path)
    if sha256_file(replacement_path) != row.get("replacement_mutation_sha256"):
        raise Qualified20NormalizationError(f"retry mutation hash mismatch: {row.get('source_id')}")


def _qualification_row_map(report: dict[str, Any], expected_ids: list[str], label: str) -> dict[str, dict[str, Any]]:
    rows = _report_rows(report, expected_ids, label)
    return {row["source_id"]: row for row in rows}


def create_combined_qualification_binding(
    *,
    selection_ids_path: Path,
    base_correction_manifest_path: Path,
    base_correction_root: Path,
    prior_run_root: Path,
    retry_run_root: Path,
    retry_correction_manifest_path: Path,
    output_report_path: Path,
    combined_source_ids_path: Path,
    combined_task_ids_path: Path,
) -> dict[str, Any]:
    """Bind the immutable prior attempt and passed retry in original order."""

    selected_ids = _read_ids(selection_ids_path, "pinned selection IDs")
    _same_order(selected_ids, EXPECTED_SELECTION_IDS, "pinned selection")
    selection_hash = sha256_file(selection_ids_path)
    if selection_hash != "63fab71f0a3e7058f81fa00664e2c52a2ad25cddf7749280c8f06b3b433b7d88":
        raise Qualified20NormalizationError("pinned selection hash mismatch")

    base_rows = _read_jsonl(base_correction_manifest_path)
    if [row.get("source_id") for row in base_rows] != selected_ids:
        raise Qualified20NormalizationError("base correction manifest does not preserve selection order")
    if len(base_rows) != 20 or len({row.get("source_id") for row in base_rows}) != 20:
        raise Qualified20NormalizationError("base correction manifest does not contain twenty unique rows")
    for row in base_rows:
        _check_correction_row({
            "schema_version": CORRECTION_ROW_SCHEMA_VERSION,
            "source_dataset": row.get("source_dataset"),
            "source_id": row.get("source_id"),
            "task_id": row.get("task_id"),
            "split": row.get("split"),
            "design_family": row.get("design_family"),
            "top_module": row.get("top_module"),
            "upstream_commit": row.get("upstream_commit"),
            "original_prompt_sha256": row.get("original_prompt_sha256"),
            "original_reference_rtl_sha256": row.get("original_reference_rtl_sha256"),
            "original_testbench_sha256": row.get("original_testbench_sha256"),
            "corrected_testbench_sha256": row.get("corrected_testbench_sha256"),
            "correction_version": row.get("correction_version"),
            "correction_reason": row.get("correction_reason"),
            "authoring_method": row.get("authoring_method"),
            "reference_modified": row.get("reference_modified"),
            "reference_copied_to_support": row.get("reference_copied_to_support"),
            "testbench_path": row.get("testbench_path"),
            "support_files": row.get("support_files"),
            "dependency_closure": row.get("dependency_closure"),
            "verification_readiness": "executable_ready",
            "negative_mutation_validation": row.get("mutation_contracts") or {},
        }, base_correction_root, BASE_CORRECTION_VERSION)

    prior_report_path = prior_run_root / "reports" / "asset_qualification_validation.json"
    prior_evidence_path = prior_run_root / "reports" / "asset_qualification_evidence.jsonl"
    prior_sidecar_path = prior_run_root / "staged" / "candidate_evidence.jsonl.runner.json"
    prior_manifest_path = prior_run_root / "input" / "candidate_manifest.jsonl"
    prior_preflight_path = prior_run_root / "reports" / "asset_qualification_preflight.json"
    prior_qualified_path = prior_run_root / "reports" / "qualified_task_ids.txt"
    prior_failed_path = prior_run_root / "reports" / "failed_or_inconclusive_task_ids.txt"
    for path in (prior_report_path, prior_evidence_path, prior_sidecar_path, prior_manifest_path, prior_preflight_path, prior_qualified_path, prior_failed_path):
        _require_regular(path)
    prior_report = _read_json(prior_report_path)
    prior_map = _qualification_row_map(prior_report, selected_ids, "prior qualification report")
    if prior_report.get("selected_tasks") != 20 or prior_report.get("qualified_tasks") != 18 or prior_report.get("failed_qualification") != 2:
        raise Qualified20NormalizationError("prior qualification counts are not the preserved 18/2 result")
    prior_failed = [source_id for source_id in selected_ids if prior_map[source_id].get("qualification_passed") is not True]
    retry_ids = ["Prob070_ece241_2013_q2", "Prob074_ece241_2014_q4"]
    if prior_failed != retry_ids:
        raise Qualified20NormalizationError("prior failed IDs do not match retry scope")
    if _read_ids(prior_qualified_path, "prior qualified IDs") != [source_id for source_id in selected_ids if source_id not in retry_ids]:
        raise Qualified20NormalizationError("prior qualified list mismatch")
    if _read_ids(prior_failed_path, "prior failed IDs") != retry_ids:
        raise Qualified20NormalizationError("prior failed list mismatch")

    retry_report_path = retry_run_root / "reports" / "asset_qualification_validation.json"
    retry_evidence_path = retry_run_root / "reports" / "asset_qualification_evidence.jsonl"
    retry_sidecar_path = retry_run_root / "staged" / "candidate_evidence.jsonl.runner.json"
    retry_manifest_path = retry_run_root / "input" / "candidate_manifest.jsonl"
    retry_preflight_path = retry_run_root / "reports" / "asset_qualification_preflight.json"
    retry_overlay_manifest_path = retry_correction_manifest_path
    retry_overlay_root = retry_overlay_manifest_path.parent
    for path in (retry_report_path, retry_evidence_path, retry_sidecar_path, retry_manifest_path, retry_preflight_path, retry_overlay_manifest_path):
        _require_regular(path)
    retry_report = _read_json(retry_report_path)
    _validate_retry_preflight(retry_run_root, retry_report)
    retry_map = _qualification_row_map(retry_report, retry_ids, "retry qualification report")
    if any(row.get("qualification_passed") is not True for row in retry_map.values()):
        raise Qualified20NormalizationError("retry qualification is not fully passed")
    retry_overlay_rows = _read_jsonl(retry_overlay_manifest_path)
    if [row.get("source_id") for row in retry_overlay_rows] != retry_ids:
        raise Qualified20NormalizationError("retry correction overlay does not match retry order")
    base_by_source = {row["source_id"]: row for row in base_rows}
    for row in retry_overlay_rows:
        _check_retry_mutation_row(row, retry_overlay_root, base_by_source[row["source_id"]])

    combined_source_ids = selected_ids
    manifest_by_source = {row["source_id"]: row for row in base_rows}
    combined_task_ids = [manifest_by_source[source_id]["task_id"] for source_id in combined_source_ids]
    _write_or_validate(combined_source_ids_path, ("\n".join(combined_source_ids) + "\n").encode("utf-8"))
    _write_or_validate(combined_task_ids_path, ("\n".join(combined_task_ids) + "\n").encode("utf-8"))

    prior_preflight = _read_json(prior_preflight_path)
    retry_preflight = _read_json(retry_preflight_path)
    rows: list[dict[str, Any]] = []
    for source_id in combined_source_ids:
        base = manifest_by_source[source_id]
        if source_id in retry_ids:
            report = retry_report
            report_path = retry_report_path
            evidence_path = retry_evidence_path
            sidecar_path = retry_sidecar_path
            qualification_manifest_path = retry_manifest_path
            qualification_source = "asset_qualification_retry_01"
            correction_manifest_hash = sha256_file(retry_overlay_manifest_path)
            qualification_row = retry_map[source_id]
            mutation_rows = 1 + int(qualification_row.get("negative_mutation_count", 0))
            detected_mutations = int(qualification_row.get("negative_mutations_detected", 0))
        else:
            report = prior_report
            report_path = prior_report_path
            evidence_path = prior_evidence_path
            sidecar_path = prior_sidecar_path
            qualification_manifest_path = prior_manifest_path
            qualification_source = "asset_qualification_attempt_01"
            correction_manifest_hash = sha256_file(base_correction_manifest_path)
            qualification_row = prior_map[source_id]
            mutation_rows = 1 + int(qualification_row.get("negative_mutation_count", 0))
            detected_mutations = int(qualification_row.get("negative_mutations_detected", 0))
        rows.append({
            "source_id": source_id,
            "task_id": base["task_id"],
            "split": base["split"],
            "qualification_source": qualification_source,
            "qualification_result": "passed",
            "qualification_status": "qualified",
            "mutation_rows": mutation_rows,
            "detected_mutations": detected_mutations,
            "reference_supplied": False,
            "support_file_count": 0,
            "corrected_testbench_sha256": base["corrected_testbench_sha256"],
            "qualification_report_sha256": sha256_file(report_path),
            "qualification_evidence_sha256": sha256_file(evidence_path),
            "raw_runner_evidence_sha256": sha256_file(report_path.parent.parent / "staged" / "candidate_evidence.jsonl"),
            "runner_sidecar_sha256": sha256_file(sidecar_path),
            "qualification_manifest_sha256": sha256_file(qualification_manifest_path),
            "correction_manifest_sha256": correction_manifest_hash,
            "original_reference_rtl_sha256": base["original_reference_rtl_sha256"],
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        })

    binding = {
        "schema_version": QUALIFICATION_BINDING_SCHEMA_VERSION,
        "run_id": retry_run_root.name,
        "qualification_scope": "qualification_only",
        "generic_manual_run_validator_applicable": False,
        "qualification_validator_authoritative": True,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "selection_ids_sha256": selection_hash,
        "base_correction_manifest_sha256": sha256_file(base_correction_manifest_path),
        "retry_correction_manifest_sha256": sha256_file(retry_overlay_manifest_path),
        "prior_qualification_report_sha256": sha256_file(prior_report_path),
        "prior_qualification_evidence_sha256": sha256_file(prior_evidence_path),
        "prior_runner_sidecar_sha256": sha256_file(prior_sidecar_path),
        "prior_candidate_manifest_sha256": sha256_file(prior_manifest_path),
        "prior_workspace_tree_sha256": prior_preflight.get("workspace_tree_sha256"),
        "retry_qualification_report_sha256": sha256_file(retry_report_path),
        "retry_qualification_evidence_sha256": sha256_file(retry_evidence_path),
        "retry_runner_sidecar_sha256": sha256_file(retry_sidecar_path),
        "retry_candidate_manifest_sha256": sha256_file(retry_manifest_path),
        "retry_workspace_tree_sha256": retry_preflight.get("workspace_tree_sha256"),
        "combined_qualified_source_ids_sha256": sha256_file(combined_source_ids_path),
        "combined_qualified_task_ids_sha256": sha256_file(combined_task_ids_path),
        "selected_task_count": 20,
        "qualified_task_count": 20,
        "failed_task_count": 0,
        "qualification_passed": True,
        "normalization_allowed": True,
        "teacher_generation_allowed": False,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "original_qualification_preserved": True,
        "retry_qualification_preserved": True,
        "source_ids": combined_source_ids,
        "task_ids": combined_task_ids,
        "rows": rows,
        "errors": [],
    }
    _write_or_validate(output_report_path, _json_bytes(binding, pretty=True))
    return {
        "ok": True,
        "binding_sha256": sha256_file(output_report_path),
        "source_ids_sha256": sha256_file(combined_source_ids_path),
        "task_ids_sha256": sha256_file(combined_task_ids_path),
        "source_ids": combined_source_ids,
        "task_ids": combined_task_ids,
        "qualified_task_count": 20,
        "failed_task_count": 0,
    }


def create_compatible_export_manifest(
    *,
    base_manifest_path: Path,
    base_correction_root: Path,
    combined_binding_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Create a v0.1 exporter view without changing the v0.2 source manifest."""

    binding = _read_json(combined_binding_path)
    if binding.get("qualification_passed") is not True or binding.get("qualified_task_count") != 20:
        raise Qualified20NormalizationError("combined qualification binding is not a passed twenty-task binding")
    rows = _read_jsonl(base_manifest_path)
    if [row.get("source_id") for row in rows] != binding.get("source_ids"):
        raise Qualified20NormalizationError("base manifest and combined binding order differ")
    converted: list[dict[str, Any]] = []
    for row in rows:
        _check_correction_row({
            "schema_version": CORRECTION_ROW_SCHEMA_VERSION,
            "source_dataset": row.get("source_dataset"),
            "source_id": row.get("source_id"),
            "task_id": row.get("task_id"),
            "split": row.get("split"),
            "design_family": row.get("design_family"),
            "top_module": row.get("top_module"),
            "upstream_commit": row.get("upstream_commit"),
            "original_prompt_sha256": row.get("original_prompt_sha256"),
            "original_reference_rtl_sha256": row.get("original_reference_rtl_sha256"),
            "original_testbench_sha256": row.get("original_testbench_sha256"),
            "corrected_testbench_sha256": row.get("corrected_testbench_sha256"),
            "correction_version": BASE_CORRECTION_VERSION,
            "correction_reason": row.get("correction_reason"),
            "authoring_method": row.get("authoring_method"),
            "reference_modified": row.get("reference_modified"),
            "reference_copied_to_support": row.get("reference_copied_to_support"),
            "testbench_path": row.get("testbench_path"),
            "support_files": row.get("support_files"),
            "dependency_closure": row.get("dependency_closure"),
            "verification_readiness": "executable_ready",
            "negative_mutation_validation": {"qualification_passed": True, "qualification_source": "combined_qualification_binding"},
        }, base_correction_root, BASE_CORRECTION_VERSION)
        converted.append({
            "schema_version": CORRECTION_ROW_SCHEMA_VERSION,
            "source_dataset": row["source_dataset"],
            "source_id": row["source_id"],
            "task_id": row["task_id"],
            "split": row["split"],
            "design_family": row["design_family"],
            "top_module": row["top_module"],
            "upstream_commit": row["upstream_commit"],
            "original_prompt_sha256": row["original_prompt_sha256"],
            "original_reference_rtl_sha256": row["original_reference_rtl_sha256"],
            "original_testbench_sha256": row["original_testbench_sha256"],
            "corrected_testbench_sha256": row["corrected_testbench_sha256"],
            "correction_version": BASE_CORRECTION_VERSION,
            "correction_reason": row["correction_reason"],
            "authoring_method": row["authoring_method"],
            "reference_modified": False,
            "reference_copied_to_support": False,
            "testbench_path": row["testbench_path"],
            "support_files": [],
            "dependency_closure": "passed",
            "verification_readiness": "executable_ready",
            "negative_mutation_validation": {
                "schema_version": "rtl_correction_negative_mutation_contract_v0.1",
                "validated_offline": True,
                "execution_deferred": False,
                "oracle_basis": "public_specification_only",
                "qualification_source": "combined_qualification_binding",
                "qualification_passed": True,
                "cases": [],
            },
        })
    _write_jsonl(output_path, converted)
    return {
        "ok": True,
        "row_count": len(converted),
        "manifest_sha256": sha256_file(output_path),
        "source_ids": [row["source_id"] for row in converted],
    }


def _workspace_tree_metadata(root: Path) -> tuple[list[str], int]:
    _require_directory(root)
    entries: list[str] = []
    regular_files = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise Qualified20NormalizationError(f"invalid private directory: {path}")
            entries.append("D:" + path.relative_to(root).as_posix())
        for name in files:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise Qualified20NormalizationError(f"invalid private file: {path}")
            entries.append("F:" + path.relative_to(root).as_posix() + ":" + sha256_file(path))
            regular_files += 1
    return entries, regular_files


def _validate_exported_normalization(
    *,
    run_root: Path,
    source_ids: list[str],
    task_ids: list[str],
    correction_manifest_path: Path,
    correction_root: Path,
    combined_binding_path: Path,
) -> dict[str, Any]:
    packet_dir = run_root / "normalization" / "packets"
    packet_paths = sorted(packet_dir.glob("batch_*.json"))
    if len(packet_paths) != 1:
        raise Qualified20NormalizationError("normalization export must contain exactly one batch")
    packet_path = packet_paths[0]
    packet = _read_json(packet_path)
    if packet.get("batch_schema_version") != GENERATION_BATCH_SCHEMA_VERSION:
        raise Qualified20NormalizationError("normalization packet schema mismatch")
    rows = packet.get("rows")
    if not isinstance(rows, list) or len(rows) != 20:
        raise Qualified20NormalizationError("normalization packet row count is not twenty")
    if [row.get("source_id") for row in rows] != source_ids or [row.get("task_id") for row in rows] != task_ids:
        raise Qualified20NormalizationError("normalization packet order or identity mismatch")
    for row in rows:
        if set(row) != PUBLIC_PACKET_FIELDS:
            raise Qualified20NormalizationError("normalization packet contains unknown or private fields")
    public_text = packet_path.read_text(encoding="utf-8").casefold()
    for marker in ("refmodule", "reference.sv", "testbench.sv", "private_assets", ".local_data", "/home/", "/tmp/", "candidate_evidence", "mutation"):
        if marker in public_text:
            raise Qualified20NormalizationError(f"public normalization packet contains forbidden marker: {marker}")

    correction_rows = _read_jsonl(correction_manifest_path)
    if [row.get("source_id") for row in correction_rows] != source_ids:
        raise Qualified20NormalizationError("export correction manifest order mismatch")
    correction_by_source = {row["source_id"]: row for row in correction_rows}
    assets_path = run_root / "private_assets" / "verification_assets.jsonl"
    assets = _read_jsonl(assets_path)
    if len(assets) != 20:
        raise Qualified20NormalizationError("private asset row count is not twenty")
    if [row.get("source_id") for row in assets] != source_ids or [row.get("task_id") for row in assets] != task_ids:
        raise Qualified20NormalizationError("private asset order or identity mismatch")
    for asset in assets:
        source_id = asset["source_id"]
        correction = correction_by_source[source_id]
        if asset.get("schema_version") != VERIFICATION_ASSET_SCHEMA_VERSION or asset.get("verification_readiness") != "executable_ready":
            raise Qualified20NormalizationError(f"asset is not executable-ready: {source_id}")
        if asset.get("support_files") != []:
            raise Qualified20NormalizationError(f"unexpected support files: {source_id}")
        hashes = asset.get("input_hashes")
        if not isinstance(hashes, dict) or hashes.get("testbench_sha256") != correction["corrected_testbench_sha256"]:
            raise Qualified20NormalizationError(f"corrected testbench hash is not bound: {source_id}")
        if hashes.get("reference_rtl_sha256") != correction["original_reference_rtl_sha256"]:
            raise Qualified20NormalizationError(f"reference hash changed: {source_id}")
        for key in ("reference_rtl_path", "testbench_path"):
            value = asset.get(key)
            if not isinstance(value, str) or value.startswith("/") or "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
                raise Qualified20NormalizationError(f"unsafe private asset path: {source_id}")
            if not value.startswith(f"workspace/{asset['task_id']}/"):
                raise Qualified20NormalizationError(f"private asset path is not task-scoped: {source_id}")
            full = run_root / "private_assets" / value
            _require_regular(full)
            expected_hash = hashes["reference_rtl_sha256"] if key == "reference_rtl_path" else hashes["testbench_sha256"]
            if sha256_file(full) != expected_hash:
                raise Qualified20NormalizationError(f"private asset hash mismatch: {source_id}")
    entries, regular_files = _workspace_tree_metadata(run_root / "private_assets" / "workspace")
    binding = _read_json(combined_binding_path)
    return {
        "ok": True,
        "packet_path": str(packet_path),
        "packet_sha256": sha256_file(packet_path),
        "packet_row_count": len(rows),
        "private_assets_manifest_sha256": sha256_file(assets_path),
        "private_workspace_regular_file_count": regular_files,
        "private_workspace_entry_count": len(entries),
        "qualification_binding_sha256": sha256_file(combined_binding_path),
        "source_ids": source_ids,
        "task_ids": task_ids,
        "public_reference_exposed": False,
        "public_testbench_exposed": False,
        "support_file_count": 0,
        "teacher_generation_allowed": binding.get("teacher_generation_allowed"),
    }


def prepare_qualified20_normalization(
    *,
    selection_ids_path: Path,
    base_correction_manifest_path: Path,
    base_correction_root: Path,
    prior_run_root: Path,
    retry_run_root: Path,
    retry_correction_manifest_path: Path,
    output_run_root: Path,
    source_input: Path,
    runs_root: Path,
) -> dict[str, Any]:
    validation_report_path = retry_run_root / "reports" / "asset_qualification_validation.json"
    freeze = freeze_retry_qualification_lists(
        retry_run_root=retry_run_root,
        validation_report_path=validation_report_path,
    )
    combined = create_combined_qualification_binding(
        selection_ids_path=selection_ids_path,
        base_correction_manifest_path=base_correction_manifest_path,
        base_correction_root=base_correction_root,
        prior_run_root=prior_run_root,
        retry_run_root=retry_run_root,
        retry_correction_manifest_path=retry_correction_manifest_path,
        output_report_path=retry_run_root / "reports" / "combined_qualification_binding.json",
        combined_source_ids_path=retry_run_root / "reports" / "combined_qualified_source_ids.txt",
        combined_task_ids_path=retry_run_root / "reports" / "combined_qualified_task_ids.txt",
    )
    if output_run_root.exists() or output_run_root.is_symlink():
        raise Qualified20NormalizationError("normalization output run already exists")
    initialize_manual_rtl_run(output_run_root.name, "VerilogEval", runs_root)
    output_reports = output_run_root / "reports"
    compatibility_manifest_path = output_reports / "qualified_correction_manifest.jsonl"
    compatibility = create_compatible_export_manifest(
        base_manifest_path=base_correction_manifest_path,
        base_correction_root=base_correction_root,
        combined_binding_path=retry_run_root / "reports" / "combined_qualification_binding.json",
        output_path=compatibility_manifest_path,
    )
    normalization_binding = {
        "schema_version": NORMALIZATION_BINDING_SCHEMA_VERSION,
        "run_id": output_run_root.name,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "selection_ids_sha256": sha256_file(selection_ids_path),
        "source_input_sha256": sha256_file(source_input),
        "combined_qualification_binding_sha256": sha256_file(retry_run_root / "reports" / "combined_qualification_binding.json"),
        "correction_manifest_sha256": sha256_file(base_correction_manifest_path),
        "qualified_export_manifest_sha256": compatibility["manifest_sha256"],
        "task_count": 20,
        "split": "train",
        "qualified_task_count": 20,
        "excluded_task_count": 0,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "normalization_llm_called": False,
        "teacher_generation_allowed": False,
        "source_ids": combined["source_ids"],
        "task_ids": combined["task_ids"],
        "errors": [],
    }
    binding_path = output_reports / "qualified_normalization_binding.json"
    _write_json(binding_path, normalization_binding)
    export_result, export_code = export_generation_normalization_batches(
        source_input,
        output_run_root / "normalization" / "packets",
        output_run_root / "private_assets",
        batch_size=20,
        source_commit=SOURCE_COMMIT,
        source_ids=combined["source_ids"],
        correction_manifest=compatibility_manifest_path,
        correction_root=base_correction_root,
        correction_version=BASE_CORRECTION_VERSION,
        source_dataset_override="VerilogEval",
        license_override="MIT",
    )
    if export_code:
        raise Qualified20NormalizationError(f"normalization export failed: {export_result}")
    validation = _validate_exported_normalization(
        run_root=output_run_root,
        source_ids=combined["source_ids"],
        task_ids=combined["task_ids"],
        correction_manifest_path=compatibility_manifest_path,
        correction_root=base_correction_root,
        combined_binding_path=retry_run_root / "reports" / "combined_qualification_binding.json",
    )
    attestation = {
        "schema_version": NORMALIZATION_ATTESTATION_SCHEMA_VERSION,
        "run_id": output_run_root.name,
        "qualification_binding_sha256": sha256_file(retry_run_root / "reports" / "combined_qualification_binding.json"),
        "normalization_binding_sha256": sha256_file(binding_path),
        "qualified_export_manifest_sha256": compatibility["manifest_sha256"],
        "packet_sha256": validation["packet_sha256"],
        "packet_row_count": 20,
        "source_ids": combined["source_ids"],
        "task_ids": combined["task_ids"],
        "public_reference_exposed": False,
        "public_testbench_exposed": False,
        "private_assets_manifest_sha256": validation["private_assets_manifest_sha256"],
        "normalization_llm_called": False,
        "teacher_generation_allowed": False,
        "errors": [],
    }
    attestation_path = output_reports / "qualified_normalization_attestation.json"
    _write_json(attestation_path, attestation)
    return {
        "ok": True,
        "freeze": freeze,
        "combined": combined,
        "compatibility_manifest": compatibility,
        "export": export_result,
        "validation": validation,
        "normalization_binding_sha256": sha256_file(binding_path),
        "attestation_sha256": sha256_file(attestation_path),
        "output_run_root": str(output_run_root),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-ids", required=True, type=Path)
    parser.add_argument("--base-correction-manifest", required=True, type=Path)
    parser.add_argument("--base-correction-root", required=True, type=Path)
    parser.add_argument("--prior-run-root", required=True, type=Path)
    parser.add_argument("--retry-run-root", required=True, type=Path)
    parser.add_argument("--retry-correction-manifest", required=True, type=Path)
    parser.add_argument("--output-run-root", required=True, type=Path)
    parser.add_argument("--source-input", required=True, type=Path)
    parser.add_argument("--runs-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare_qualified20_normalization(
            selection_ids_path=args.selection_ids,
            base_correction_manifest_path=args.base_correction_manifest,
            base_correction_root=args.base_correction_root,
            prior_run_root=args.prior_run_root,
            retry_run_root=args.retry_run_root,
            retry_correction_manifest_path=args.retry_correction_manifest,
            output_run_root=args.output_run_root,
            source_input=args.source_input,
            runs_root=args.runs_root,
        )
    except (OSError, UnicodeError, Qualified20NormalizationError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({
        "ok": True,
        "output_run_root": result["output_run_root"],
        "qualified_task_count": result["combined"]["qualified_task_count"],
        "failed_task_count": result["combined"]["failed_task_count"],
        "retry_qualified_task_count": len(result["freeze"]["qualified_source_ids"]),
        "retry_failed_task_count": len(result["freeze"]["failed_or_inconclusive_source_ids"]),
        "combined_binding_sha256": result["combined"]["binding_sha256"],
        "packet_sha256": result["validation"]["packet_sha256"],
        "packet_row_count": result["validation"]["packet_row_count"],
        "private_assets_manifest_sha256": result["validation"]["private_assets_manifest_sha256"],
        "normalization_llm_called": False,
        "teacher_generation_allowed": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASE_CORRECTION_VERSION",
    "EXPECTED_SELECTION_IDS",
    "Qualified20NormalizationError",
    "create_combined_qualification_binding",
    "create_compatible_export_manifest",
    "freeze_retry_qualification_lists",
    "prepare_qualified20_normalization",
    "sha256_file",
]
