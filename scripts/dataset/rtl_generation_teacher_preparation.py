"""Prepare and validate a qualified public RTL teacher-generation packet set.

This module is deliberately control-plane only.  It binds an already-qualified
and normalized train-only task subset, validates the immutable lineage, and
checks the public packet files produced by the existing exporter.  It never
calls a model, invokes a subprocess, reads private HDL, or executes RTL.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from scripts.dataset.rtl_manual_teacher_verification import (
    PACKET_SCHEMA_VERSION,
    SHA256_RE,
    WorkflowError,
    _contains_symlink,
    _generation_markdown,
    _is_hard_link,
    _load_packet,
    _load_jsonl,
    _private_string_leak,
    _public_task,
    _read_json,
    _sha256_file,
    _walk_strings,
)


BINDING_SCHEMA_VERSION = "rtl_generation_teacher_binding_v0.1"
PACKET_VALIDATION_SCHEMA_VERSION = "rtl_generation_teacher_packet_validation_v0.1"
PACKET_SET_HASH_SCHEMA_VERSION = "rtl_generation_teacher_packet_set_hash_v0.1"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
CORRECTION_VERSION = "assetfix_v003"
QUALIFIED_TASK_COUNT = 16
TRAIN_SPLIT = "train"

EXPECTED_QUALIFIED_BINDING_SCHEMA = "rtl_generation_qualified_subset_binding_v0.1"
EXPECTED_QUALIFIED_LIST_SHA256 = "62c2216d3ed2b486f8540f6c25e321c52f960f702be12a001c0ebcf4b1c818e1"
EXPECTED_QUALIFIED_SOURCE_LIST_SHA256 = "9034d5e22993b2476532a0e35c35bc8e67e9312418b55c3de23b3e0cdca1b9d0"
EXPECTED_EXCLUDED_TASK_LIST_SHA256 = "d53b327a37a78ec724e70f0cdc48f4fba069ba9f500726da10cb79612240cb72"
EXPECTED_EXCLUDED_SOURCE_LIST_SHA256 = "494a21e0744899e72e89c53e745f25093a56f83dd86494b65fbf76cde8f1ca07"
EXPECTED_QUALIFIED_CORRECTION_MANIFEST_SHA256 = "5fbebe595e5650ddd392231e3883796028a56fc7546b836e6c6c36c3fffde734"
EXPECTED_CORRECTION_MANIFEST_SHA256 = "2b7cbe7a82f0b73e9960216254565600a9df46bebd66c71fa45f0f401501b8c1"
EXPECTED_QUALIFICATION_REPORT_SHA256 = "8b3ac80a6cea697b29fc5523529791811b62a6f035850083adbbbcf4ba3c7729"
EXPECTED_QUALIFICATION_EVIDENCE_SHA256 = "b7f3ac3a9127bd9eeaf9c033da5eb08fa36142e13d941d9270c5e47a9810c104"
EXPECTED_RAW_QUALIFICATION_EVIDENCE_SHA256 = "954dbb606f721c568df58f9032ce63b72cd6e3e5edb302210cba67fa9d1e4eae"
EXPECTED_RUNNER_SIDECAR_SHA256 = "35c81fe662908ffb383991b0e505a3486330a9fe1cfaac53c6b07ec272f93e4f"

_FORBIDDEN_PUBLIC_MARKERS = (
    "reference.sv",
    "testbench.sv",
    ".local_data",
    "private_assets",
    "refmodule",
)


class TeacherPreparationError(ValueError):
    """Raised when the teacher-generation boundary cannot be proven safe."""


def sha256_file(path: Path) -> str:
    """Hash one unique regular file without following unsafe tree entries."""

    if _contains_symlink(path) or path.is_symlink() or not path.is_file():
        raise TeacherPreparationError(f"required input is not a regular file: {path}")
    if _is_hard_link(path):
        raise TeacherPreparationError(f"required input is a hard-link alias: {path}")
    return _sha256_file(path)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = _read_json(path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise TeacherPreparationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise TeacherPreparationError(f"{label} must be a JSON object")
    return value


def _load_ids(path: Path, label: str) -> list[str]:
    if _contains_symlink(path) or path.is_symlink() or not path.is_file():
        raise TeacherPreparationError(f"{label} is not a regular file")
    if _is_hard_link(path):
        raise TeacherPreparationError(f"{label} is a hard-link alias")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise TeacherPreparationError(f"{label} contains duplicate identities")
    if not values:
        raise TeacherPreparationError(f"{label} is empty")
    return values


def _load_optional_ids(path: Path, label: str) -> list[str]:
    """Load an allowlist that may be empty, while keeping the same safety checks."""

    if _contains_symlink(path) or path.is_symlink() or not path.is_file():
        raise TeacherPreparationError(f"{label} is not a regular file")
    if _is_hard_link(path):
        raise TeacherPreparationError(f"{label} is a hard-link alias")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise TeacherPreparationError(f"{label} contains duplicate identities")
    return values


def _write_exclusive_json(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise TeacherPreparationError(f"refusing to replace existing output: {path}")
    if _contains_symlink(path.parent):
        raise TeacherPreparationError(f"output parent contains a symlink: {path.parent}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _contains_symlink(path.parent):
        raise TeacherPreparationError(f"output parent became unsafe: {path.parent}")
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(content)
        os.chmod(path, 0o600)
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise TeacherPreparationError(f"could not write output: {path}") from exc


def _require_hash(path: Path, expected: str, label: str) -> str:
    actual = sha256_file(path)
    if actual != expected:
        raise TeacherPreparationError(f"{label} hash mismatch")
    return actual


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise TeacherPreparationError(f"{label} must be a SHA-256 string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise TeacherPreparationError(f"{label} must be a SHA-256 string") from exc
    return value


def _text_list_sha256(values: list[str]) -> str:
    return hashlib.sha256(("\n".join(values) + "\n").encode("utf-8")).hexdigest()


def _assert_no_forbidden_markers(value: Any, label: str) -> None:
    for text in _walk_strings(value):
        lowered = text.casefold()
        if _private_string_leak(text):
            raise TeacherPreparationError(f"{label} contains an absolute or local path")
        for marker in _FORBIDDEN_PUBLIC_MARKERS:
            if marker in lowered:
                raise TeacherPreparationError(f"{label} contains forbidden marker: {marker}")


def _assert_empty_generation_state(run_root: Path) -> None:
    response_dir = run_root / "teacher" / "responses"
    if not response_dir.is_dir() or _contains_symlink(response_dir):
        raise TeacherPreparationError("teacher response directory is missing or unsafe")
    if any(response_dir.iterdir()):
        raise TeacherPreparationError("teacher response directory is not empty")
    for relative in (
        "teacher/candidate_records.jsonl",
        "verification/generation_attempts.jsonl",
    ):
        if (run_root / relative).exists() or (run_root / relative).is_symlink():
            raise TeacherPreparationError(f"pre-generation output already exists: {relative}")
    for directory in (run_root / "verification", run_root / "repairs"):
        if not directory.is_dir() or _contains_symlink(directory):
            raise TeacherPreparationError(f"pre-generation directory is missing or unsafe: {directory.name}")
        if any(directory.iterdir()):
            raise TeacherPreparationError(f"pre-generation directory is not empty: {directory.name}")


def _qualified_rows(binding: dict[str, Any]) -> list[dict[str, str]]:
    rows = binding.get("rows")
    if not isinstance(rows, list) or not rows:
        raise TeacherPreparationError("qualified binding rows are empty")
    result: list[dict[str, str]] = []
    seen_sources: set[str] = set()
    seen_tasks: set[str] = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise TeacherPreparationError(f"qualified binding row {index} is not an object")
        source_id = row.get("source_id")
        task_id = row.get("task_id")
        if not isinstance(source_id, str) or not source_id or not isinstance(task_id, str) or not task_id:
            raise TeacherPreparationError(f"qualified binding row {index} identity is invalid")
        if source_id in seen_sources or task_id in seen_tasks:
            raise TeacherPreparationError("qualified binding contains duplicate identities")
        seen_sources.add(source_id)
        seen_tasks.add(task_id)
        if row.get("qualification_result") != "passed" or row.get("qualification_status") != "qualified":
            raise TeacherPreparationError(f"qualified binding row {index} is not passed")
        if row.get("reference_supplied") is not False or row.get("support_files") != []:
            raise TeacherPreparationError(f"qualified binding row {index} privacy contract failed")
        result.append({"source_id": source_id, "task_id": task_id})
    return result


def _validate_qualified_binding(binding_path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    binding = _load_object(binding_path, "qualified subset binding")
    if binding.get("schema_version") != EXPECTED_QUALIFIED_BINDING_SCHEMA:
        raise TeacherPreparationError("qualified subset binding schema mismatch")
    correction_version = binding.get("correction_version")
    if not isinstance(correction_version, str) or not correction_version:
        raise TeacherPreparationError("qualified subset binding correction version is invalid")
    rows = _qualified_rows(binding)
    task_count = len(rows)
    failed_task_count = binding.get("failed_task_count")
    if not isinstance(failed_task_count, int) or failed_task_count < 0:
        raise TeacherPreparationError("qualified subset binding failed-task count is invalid")
    checks = {
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "normalization_allowed": True,
        "teacher_generation_allowed": False,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
    }
    for key, expected in checks.items():
        if binding.get(key) != expected:
            raise TeacherPreparationError(f"qualified subset binding mismatch: {key}")
    if binding.get("qualified_task_count") != task_count:
        raise TeacherPreparationError("qualified subset binding task count mismatch")
    for field in (
        "selection_ids_sha256",
        "correction_manifest_sha256",
        "qualification_report_sha256",
        "qualification_evidence_sha256",
        "raw_runner_evidence_sha256",
        "runner_sidecar_sha256",
        "qualified_task_ids_sha256",
        "failed_task_ids_sha256",
    ):
        _require_sha256(binding.get(field), f"qualified subset binding {field}")
    return binding, rows


def _ordered_identities(
    tasks: list[dict[str, Any]],
    qualified_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    by_task = {row.get("task_id"): row for row in tasks}
    if len(by_task) != len(tasks):
        raise TeacherPreparationError("generation task manifest contains duplicate task IDs")
    identities: list[dict[str, str]] = []
    for item in qualified_rows:
        task = by_task.get(item["task_id"])
        if task is None or task.get("source_id") != item["source_id"]:
            raise TeacherPreparationError("qualified task identity is not present in generation tasks")
        if task.get("top_module") != "TopModule":
            raise TeacherPreparationError("qualified task has an unexpected top module")
        identities.append({
            "source_id": item["source_id"],
            "task_id": item["task_id"],
            "top_module": task["top_module"],
        })
    if [row["task_id"] for row in identities] != [row.get("task_id") for row in qualified_rows]:
        raise TeacherPreparationError("qualified task order changed")
    return identities


def create_teacher_generation_binding(
    run_root: Path,
    *,
    rtlspecializer_commit: str,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Create the exclusive binding report for a qualified train-only run."""

    try:
        if not isinstance(rtlspecializer_commit, str) or len(rtlspecializer_commit) != 40:
            raise TeacherPreparationError("RTLSpecializer commit must be a 40-character SHA")
        int(rtlspecializer_commit, 16)
        run_root = run_root.resolve()
        output_path = output_path or run_root / "reports" / "teacher_generation_binding.json"
        _assert_empty_generation_state(run_root)
        tasks_path = run_root / "tasks" / "generation_tasks.jsonl"
        assets_path = run_root / "tasks" / "verification_assets.jsonl"
        qualified_binding_path = run_root / "reports" / "qualified_subset_binding.json"
        qualified_manifest_path = run_root / "reports" / "qualified_correction_manifest.jsonl"
        qualified_ids_path = run_root / "reports" / "qualified_source_ids.txt"
        failed_ids_path = run_root / "reports" / "failed_source_ids.txt"
        qualified_binding, qualified_rows = _validate_qualified_binding(qualified_binding_path)
        tasks = _load_jsonl(tasks_path)
        assets = _load_jsonl(assets_path)
        task_count = len(qualified_rows)
        if len(tasks) != task_count or len(assets) != task_count:
            raise TeacherPreparationError("qualified task and asset manifests do not match the qualified row count")
        for task in tasks:
            _public_task(task)
        identities = _ordered_identities(tasks, qualified_rows)
        qualified_source_ids = _load_ids(qualified_ids_path, "qualified source IDs")
        if qualified_source_ids != [row["source_id"] for row in identities]:
            raise TeacherPreparationError("qualified source ID list order or identity mismatch")
        failed_source_ids = _load_optional_ids(failed_ids_path, "excluded source IDs")
        qualified_task_ids = [row["task_id"] for row in identities]
        qualified_task_ids_hash = _text_list_sha256(qualified_task_ids)
        asset_by_task = {asset.get("task_id"): asset for asset in assets}
        if len(asset_by_task) != task_count:
            raise TeacherPreparationError("verification asset manifest contains duplicate task IDs")
        correction_rows = _load_jsonl(qualified_manifest_path)
        correction_by_source = {row.get("source_id"): row for row in correction_rows}
        if len(correction_rows) != task_count or [row.get("source_id") for row in correction_rows] != [row["source_id"] for row in identities]:
            raise TeacherPreparationError("qualified correction manifest identity mismatch")
        correction_version = qualified_binding["correction_version"]
        for identity in identities:
            asset = asset_by_task.get(identity["task_id"])
            correction = correction_by_source[identity["source_id"]]
            if asset is None or asset.get("source_id") != identity["source_id"] or asset.get("top_module") != "TopModule":
                raise TeacherPreparationError("verification asset identity mismatch")
            if asset.get("verification_readiness") != "executable_ready" or asset.get("readiness_reasons") != []:
                raise TeacherPreparationError("qualified asset is not executable-ready")
            if asset.get("support_files") != []:
                raise TeacherPreparationError("qualified asset declares support files")
            if asset.get("input_hashes", {}).get("testbench_sha256") != correction.get("corrected_testbench_sha256"):
                raise TeacherPreparationError("corrected testbench hash is not bound to the asset")
            if correction.get("correction_version") != correction_version:
                raise TeacherPreparationError("correction version mismatch")
            if correction.get("verification_readiness") != "executable_ready" or correction.get("qualification_status") != "qualified":
                raise TeacherPreparationError("correction row is not qualified")
        packet_path = run_root / "normalization" / "packets" / "batch_001.json"
        response_path = run_root / "normalization" / "responses" / "batch_001_response.json"
        exchange_path = run_root / "reports" / "normalization_exchange.json"
        assembly_path = run_root / "reports" / "normalization_assembly.json"
        qualified_correction_hash = sha256_file(qualified_manifest_path)
        packet_hash = sha256_file(packet_path)
        response_hash = sha256_file(response_path)
        exchange = _load_object(exchange_path, "normalization exchange report")
        assembly = _load_object(assembly_path, "normalization assembly report")
        if exchange.get("normalization_status") != "validated" or exchange.get("row_count") != task_count:
            raise TeacherPreparationError("normalization exchange is not validated")
        if exchange.get("packet_sha256") != packet_hash or exchange.get("canonical_response_sha256") != response_hash:
            raise TeacherPreparationError("normalization exchange hash binding mismatch")
        if assembly.get("ok") is not True or assembly.get("task_count") != task_count or assembly.get("asset_count") != task_count:
            raise TeacherPreparationError("normalization assembly is not complete")
        if assembly.get("tasks_sha256") != sha256_file(tasks_path) or assembly.get("assets_sha256") != sha256_file(assets_path):
            raise TeacherPreparationError("normalization assembly output hash mismatch")
        if assembly.get("qualified_task_ids_sha256") != qualified_task_ids_hash:
            raise TeacherPreparationError("normalization assembly qualified-list binding mismatch")
        artifacts = {
            "normalization_packet": packet_hash,
            "normalization_response": response_hash,
            "normalization_exchange_report": sha256_file(exchange_path),
            "normalization_assembly_report": sha256_file(assembly_path),
            "qualified_task_list": _text_list_sha256(qualified_task_ids),
            "qualified_source_list": sha256_file(qualified_ids_path),
            "excluded_task_list": qualified_binding["failed_task_ids_sha256"],
            "excluded_source_list": sha256_file(failed_ids_path),
            "qualification_report": qualified_binding["qualification_report_sha256"],
            "qualification_evidence": qualified_binding["qualification_evidence_sha256"],
            "qualification_runner_evidence": qualified_binding["raw_runner_evidence_sha256"],
            "qualification_runner_sidecar": qualified_binding["runner_sidecar_sha256"],
            "qualified_subset_binding": sha256_file(qualified_binding_path),
            "asset_correction_manifest": qualified_binding["correction_manifest_sha256"],
            "qualified_correction_manifest": qualified_correction_hash,
            "generation_tasks": sha256_file(tasks_path),
            "verification_assets": sha256_file(assets_path),
            "private_asset_manifest": sha256_file(run_root / "private_assets" / "verification_assets.jsonl"),
            "run_manifest": sha256_file(run_root / "run_manifest.json"),
        }
        binding = {
            "schema_version": BINDING_SCHEMA_VERSION,
            "run_id": run_root.name,
            "task_count": task_count,
            "split": TRAIN_SPLIT,
            "correction_version": correction_version,
            "rtlspecializer_commit": rtlspecializer_commit,
            "source_commit": SOURCE_COMMIT,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "frozen_split_sha256": FROZEN_SPLIT_SHA256,
            "qualification_passed": True,
            "qualification_scope": "qualification_only",
            "reference_rtl_supplied": False,
            "support_file_count": 0,
            "excluded_source_ids": failed_source_ids,
            "excluded_task_ids_sha256": qualified_binding["failed_task_ids_sha256"],
            "qualified_order": identities,
            "artifact_hashes": artifacts,
            "packet_export_allowed": True,
            "teacher_response_allowed": False,
            "teacher_response_requires_packet_validation": True,
            "teacher_generation_allowed": False,
            "qualification_binding_sha256": sha256_file(qualified_binding_path),
            "errors": [],
        }
        if set(binding["excluded_source_ids"]) & {row["source_id"] for row in identities}:
            raise TeacherPreparationError("qualified order unexpectedly includes an excluded source")
        _assert_no_forbidden_markers(binding, "teacher-generation binding")
        _write_exclusive_json(output_path, binding)
        return {
            "ok": True,
            "binding_path": str(output_path),
            "binding_sha256": sha256_file(output_path),
            "task_count": task_count,
            "split": TRAIN_SPLIT,
            "qualified_order": identities,
            "excluded_source_ids": failed_source_ids,
            "reference_rtl_supplied": False,
            "support_file_count": 0,
            "packet_export_allowed": True,
            "teacher_response_allowed": False,
            "errors": [],
        }, 0
    except (TeacherPreparationError, WorkflowError, OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1


def packet_set_sha256(packet_dir: Path) -> tuple[str, list[dict[str, str]]]:
    """Return a deterministic digest over packet filenames and file hashes."""

    if _contains_symlink(packet_dir) or packet_dir.is_symlink() or not packet_dir.is_dir():
        raise TeacherPreparationError("packet directory is not a safe directory")
    files = []
    for path in sorted(packet_dir.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file() or _is_hard_link(path):
            raise TeacherPreparationError("packet directory contains an unsafe entry")
        files.append({"path": path.name, "sha256": sha256_file(path)})
    canonical = {
        "schema_version": PACKET_SET_HASH_SCHEMA_VERSION,
        "files": files,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), files


def _canonical_row_sha256(row: dict[str, Any]) -> str:
    encoded = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_teacher_generation_handoff(
    *,
    run_root: Path,
    binding_path: Path,
    tasks_path: Path,
    assets_path: Path,
    private_assets_root: Path,
    candidate_record: dict[str, Any],
    task: dict[str, Any],
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Bind one candidate to the validated qualified-16 teacher lineage."""

    del run_root  # The sibling report location is anchored by binding_path.
    binding = _load_object(binding_path, "teacher-generation binding")
    if binding.get("schema_version") != BINDING_SCHEMA_VERSION:
        raise TeacherPreparationError("teacher-generation binding schema mismatch")
    packet_validation_path = binding_path.parent / "teacher_generation_packet_validation.json"
    packet_report = _load_object(packet_validation_path, "teacher-generation packet validation report")
    binding_hash = sha256_file(binding_path)
    packet_report_hash = sha256_file(packet_validation_path)
    if packet_report.get("ok") is not True or packet_report.get("teacher_response_allowed") is not True:
        raise TeacherPreparationError("teacher-generation packet validation has not passed")
    if packet_report.get("binding_sha256") != binding_hash:
        raise TeacherPreparationError("packet validation report is bound to a different teacher binding")
    identities = binding.get("qualified_order")
    if not isinstance(identities, list) or not identities:
        raise TeacherPreparationError("teacher-generation binding has no qualified order")
    if binding.get("task_count") != len(identities) or binding.get("split") != TRAIN_SPLIT:
        raise TeacherPreparationError("teacher-generation binding task or split count is invalid")
    if binding.get("qualification_passed") is not True or binding.get("reference_rtl_supplied") is not False:
        raise TeacherPreparationError("teacher-generation binding is not qualified and private-safe")
    if binding.get("support_file_count") != 0 or not isinstance(binding.get("correction_version"), str):
        raise TeacherPreparationError("teacher-generation binding asset contract is invalid")
    if candidate_record.get("task_id") != task.get("task_id") or candidate_record.get("source_id") != task.get("source_id"):
        raise TeacherPreparationError("candidate/task identity mismatch")
    if candidate_record.get("attempt") != 1 or candidate_record.get("candidate_id") != f"{task['task_id']}_attempt_01":
        raise TeacherPreparationError("candidate is not deterministic attempt 1")
    candidate = candidate_record.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("top_module") != task.get("top_module"):
        raise TeacherPreparationError("candidate top-module identity mismatch")
    candidate_hash = hashlib.sha256(str(candidate.get("rtl", "")).encode("utf-8")).hexdigest()
    if candidate_record.get("candidate_sha256") != candidate_hash:
        raise TeacherPreparationError("candidate hash mismatch")
    identity = next((row for row in identities if row.get("task_id") == task.get("task_id")), None) if isinstance(identities, list) else None
    if identity is None or identity.get("source_id") != task.get("source_id") or identity.get("top_module") != task.get("top_module"):
        raise TeacherPreparationError("task is not in the qualified teacher order")
    packet_rows = packet_report.get("packets")
    packet = next((row for row in packet_rows if row.get("task_id") == task.get("task_id")), None) if isinstance(packet_rows, list) else None
    if packet is None or packet.get("source_id") != task.get("source_id") or packet.get("top_module") != task.get("top_module"):
        raise TeacherPreparationError("task is not in the validated teacher packet set")
    if candidate_record.get("packet_id") != packet.get("packet_id"):
        raise TeacherPreparationError("candidate packet identity mismatch")
    hashes = binding.get("artifact_hashes")
    if not isinstance(hashes, dict):
        raise TeacherPreparationError("teacher-generation binding artifact hashes are missing")
    required_artifacts = (
        "normalization_packet",
        "normalization_response",
        "qualified_task_list",
        "qualification_evidence",
        "qualification_runner_sidecar",
    )
    for field in required_artifacts:
        if not isinstance(hashes.get(field), str) or SHA256_RE.fullmatch(hashes[field]) is None:
            raise TeacherPreparationError(f"teacher-generation binding artifact hash is invalid: {field}")
    if not isinstance(asset.get("support_files"), list) or asset.get("support_files") != []:
        raise TeacherPreparationError("qualified asset declares support files")
    testbench_path = private_assets_root / asset["testbench_path"]
    testbench_hash = sha256_file(testbench_path)
    if testbench_hash != asset.get("input_hashes", {}).get("testbench_sha256"):
        raise TeacherPreparationError("private corrected testbench hash does not match the asset record")
    if asset.get("verification_readiness") != "executable_ready" or asset.get("readiness_reasons") != []:
        raise TeacherPreparationError("asset is not executable-ready")
    task_hash = _canonical_row_sha256(task)
    asset_hash = _canonical_row_sha256(asset)
    return {
        "schema_version": "rtl_generation_teacher_handoff_binding_v0.1",
        "teacher_generation_binding_sha256": binding_hash,
        "packet_validation_report_sha256": packet_report_hash,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "candidate_id": candidate_record["candidate_id"],
        "attempt": 1,
        "top_module": task["top_module"],
        "normalization_packet_sha256": hashes["normalization_packet"],
        "normalization_response_sha256": hashes["normalization_response"],
        "qualified_task_list_sha256": hashes["qualified_task_list"],
        "qualification_binding_sha256": binding["qualification_binding_sha256"],
        "qualification_evidence_sha256": hashes["qualification_evidence"],
        "qualification_runner_sidecar_sha256": hashes["qualification_runner_sidecar"],
        "corrected_testbench_sha256": testbench_hash,
        "task_record_sha256": task_hash,
        "asset_record_sha256": asset_hash,
        "correction_version": binding["correction_version"],
        "source_commit": binding["source_commit"],
        "source_tree_sha256": binding["source_tree_sha256"],
        "frozen_split_sha256": binding["frozen_split_sha256"],
        "qualification_passed": True,
        "reference_rtl_supplied": False,
        "support_files": [],
    }


def validate_teacher_packet_set(
    run_root: Path,
    *,
    binding_path: Path | None = None,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Validate the complete public initial packet set and write one report."""

    try:
        run_root = run_root.resolve()
        binding_path = binding_path or run_root / "reports" / "teacher_generation_binding.json"
        output_path = output_path or run_root / "reports" / "teacher_generation_packet_validation.json"
        binding = _load_object(binding_path, "teacher-generation binding")
        if binding.get("schema_version") != BINDING_SCHEMA_VERSION:
            raise TeacherPreparationError("teacher-generation binding schema mismatch")
        if binding.get("packet_export_allowed") is not True or binding.get("teacher_response_allowed") is not False:
            raise TeacherPreparationError("teacher-generation binding is not at the packet-export boundary")
        identities = binding.get("qualified_order")
        if not isinstance(identities, list) or not identities:
            raise TeacherPreparationError("teacher-generation binding has an invalid qualified order")
        task_count = len(identities)
        if binding.get("task_count") not in (None, task_count):
            raise TeacherPreparationError("teacher-generation binding task count does not match qualified order")
        tasks = _load_jsonl(run_root / "tasks" / "generation_tasks.jsonl")
        by_task = {row.get("task_id"): row for row in tasks}
        if len(by_task) != task_count:
            raise TeacherPreparationError("generation task manifest does not match the qualified row count")
        packet_dir = run_root / "teacher" / "packets"
        if _contains_symlink(packet_dir) or packet_dir.is_symlink() or not packet_dir.is_dir():
            raise TeacherPreparationError("teacher packet directory is missing or unsafe")
        if stat.S_IMODE(packet_dir.stat().st_mode) != 0o700:
            raise TeacherPreparationError("teacher packet directory must be mode 0700")
        expected_names = {
            f"packet_{index:04d}.{suffix}"
            for index in range(1, task_count + 1)
            for suffix in ("json", "md")
        }
        actual_names = {path.name for path in packet_dir.iterdir()}
        if actual_names != expected_names:
            raise TeacherPreparationError("teacher packet directory has missing or unexpected entries")
        packet_records = []
        for index, identity in enumerate(identities, 1):
            json_path = packet_dir / f"packet_{index:04d}.json"
            md_path = packet_dir / f"packet_{index:04d}.md"
            for path in (json_path, md_path):
                metadata = path.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise TeacherPreparationError(f"packet entry is not a unique regular file: {path.name}")
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise TeacherPreparationError(f"packet file must be mode 0600: {path.name}")
            packet = _load_packet(json_path)
            if packet.get("packet_kind") != "initial" or packet.get("target_attempt") != 1 or packet.get("row_count") != 1:
                raise TeacherPreparationError(f"packet {index} is not a one-row initial packet")
            task = packet["rows"][0]["task"]
            expected_task = by_task.get(identity.get("task_id"))
            if expected_task is None or task != expected_task:
                raise TeacherPreparationError(f"packet {index} task content or order mismatch")
            if task.get("source_id") != identity.get("source_id") or task.get("top_module") != identity.get("top_module"):
                raise TeacherPreparationError(f"packet {index} identity mismatch")
            expected_markdown = _generation_markdown(packet)
            if md_path.read_text(encoding="utf-8") != expected_markdown:
                raise TeacherPreparationError(f"packet {index} Markdown does not match the generator")
            _assert_no_forbidden_markers(task, f"packet {index} public task")
            packet_records.append({
                "packet_number": index,
                "packet_id": packet["packet_id"],
                "task_id": task["task_id"],
                "source_id": task["source_id"],
                "top_module": task["top_module"],
                "json_filename": json_path.name,
                "json_sha256": sha256_file(json_path),
                "markdown_filename": md_path.name,
                "markdown_sha256": sha256_file(md_path),
            })
        packet_hash, file_hashes = packet_set_sha256(packet_dir)
        _assert_empty_generation_state(run_root)
        binding_hash = sha256_file(binding_path)
        report = {
            "ok": True,
            "schema_version": PACKET_VALIDATION_SCHEMA_VERSION,
            "run_id": run_root.name,
            "binding_sha256": binding_hash,
            "packet_set_sha256": packet_hash,
            "packet_set_hash_schema": PACKET_SET_HASH_SCHEMA_VERSION,
            "packet_count": task_count,
            "row_count": task_count,
            "qualified_order": [
                {"source_id": row["source_id"], "task_id": row["task_id"], "top_module": row["top_module"]}
                for row in identities
            ],
            "packets": packet_records,
            "files": file_hashes,
            "public_only": True,
            "private_data_exposed": False,
            "reference_rtl_exposed": False,
            "testbench_content_exposed": False,
            "support_file_content_exposed": False,
            "teacher_response_allowed": True,
            "candidate_generation_started": False,
            "errors": [],
        }
        _write_exclusive_json(output_path, report)
        return report, 0
    except (TeacherPreparationError, WorkflowError, OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1


__all__ = [
    "BINDING_SCHEMA_VERSION",
    "PACKET_SET_HASH_SCHEMA_VERSION",
    "PACKET_VALIDATION_SCHEMA_VERSION",
    "TeacherPreparationError",
    "create_teacher_generation_binding",
    "packet_set_sha256",
    "sha256_file",
    "validate_teacher_generation_handoff",
    "validate_teacher_packet_set",
]
