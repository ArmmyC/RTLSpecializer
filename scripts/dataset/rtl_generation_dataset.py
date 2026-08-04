"""Package accepted RTLBench attempts into generation SFT rows.

The package step is deliberately read-only with respect to RTL and private
verification assets.  It consumes already validated public tasks, candidate
records, sanitized attempt history, and optional canonical evidence/sidecar
metadata.  It never invokes a model, subprocess, compiler, simulator, or EDA
tool.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any

from scripts.dataset.rtl_generation_inventory import SPLIT_SCHEMA_VERSION
from scripts.dataset.rtl_generation_preparation import GENERATION_TASK_SCHEMA_VERSION
from scripts.dataset.rtl_manual_teacher_verification import (
    ATTEMPT_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    PROFILE,
    REQUESTED_CHECKS,
    _validate_checks,
    _validate_mismatch,
    _validate_toolchain,
    WorkflowError,
)


GENERATION_SFT_SCHEMA_VERSION = "rtl_generation_sft_row_v0.1"
PACKAGE_SCHEMA_VERSION = "rtl_verified_generation_package_v0.1"
DATASET_NAME = "verilog_eval_verified_generation_v0_1"
DATASET_STAGE = "verified_generation_sft"
REVIEW_STATUS = "automated_verified_unreviewed"
APPROVAL_STATUS = "not_approved"
SYSTEM_PROMPT = "Generate synthesizable SystemVerilog that exactly follows the public specification and interface. Return only the implementation."
SPLITS = ("train", "validation", "test")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    "reference.sv",
    "testbench.sv",
    "candidate_evidence",
    "rtlbench",
    "iverilog",
    "verilator",
    "yosys",
    "vvp",
)
PACKAGE_PRIVATE_CONTENT_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    "reference.sv",
    "reference rtl",
    "refmodule",
    "testbench.sv",
    "module tb",
    "candidate_evidence",
    "mutation",
)

SMOKE_SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SMOKE_SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
SMOKE_FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
SMOKE_RTLBENCH_COMMIT = "fcad47eb03e469097432229e1285b9239fd23a00"
SMOKE_RTLBENCH_IMAGE = "sha256:004331efd280c2c94a7a25d920f9e008c0f552237d0d66902806a327033ead9b"
SMOKE_RUNNER_PROFILE = "pilot-docker"
SMOKE_CORRECTION_VERSION = "assetfix_v002"
SMOKE_SOURCE_IDS = (
    "Prob001_zero",
    "Prob020_mt2015_eq2",
    "Prob071_always_casez",
    "Prob048_m2014_q4c",
    "Prob079_fsm3onehot",
)
SMOKE_TASK_IDS = (
    "rtlgen_verilogeval_prob001_zero_eaec15c148f9",
    "rtlgen_verilogeval_prob020_mt2015_eq2_aec940375a36",
    "rtlgen_verilogeval_prob071_always_casez_35df92a35797",
    "rtlgen_verilogeval_prob048_m2014_q4c_7379fc4191bf",
    "rtlgen_verilogeval_prob079_fsm3onehot_e3cf6755e024",
)
SANITIZED_COMPILE_WARNING_RE = re.compile(
    r"^compile: returncode=0 <source omitted>:[^\n]*$"
)
PACKAGE_LINEAGE_SCHEMA_VERSION = "rtl_dataset_package_lineage_v0.1"
RECOVERY_AUTHORIZATION_SCHEMA_VERSION = "rtl_dataset_packaging_recovery_authorization_v0.1"
RECOVERY_PACKAGE_ID = "rtl_generation_smoke_v001_retry_01"
RECOVERY_PARENT_PACKAGE_ID = "rtl_generation_smoke_v001"
RECOVERY_REASON = "packaging_source_defect"
QUALIFIED_SUBSET_BINDING_SCHEMA = "rtl_generation_qualified_subset_binding_v0.1"
QUALIFIED_SUBSET_CORRECTION_VERSION = "assetfix_v003"
QUALIFIED_SUBSET_REPORT_SCHEMA = "rtl_asset_qualification_report_v0.1"


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise WorkflowError(f"input must not be a symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise WorkflowError(f"input must not be a symlink: {path}")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise WorkflowError(f"could not read {path}: {exc}") from exc
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"malformed JSON at {path.name}:{index}") from exc
        if not isinstance(value, dict):
            raise WorkflowError(f"JSONL row at {path.name}:{index} is not an object")
        rows.append(value)
    if not rows and not allow_empty:
        raise WorkflowError(f"JSONL input is empty: {path.name}")
    return rows


def _private_marker_errors(value: Any, label: str) -> list[str]:
    errors: list[str] = []
    if isinstance(value, str):
        lowered = value.casefold()
        for marker in PRIVATE_MARKERS:
            if marker.casefold() in lowered:
                errors.append(f"{label} contains a private/tool marker: {marker}")
    elif isinstance(value, dict):
        for key, item in value.items():
            errors.extend(_private_marker_errors(key, f"{label}.key"))
            errors.extend(_private_marker_errors(item, f"{label}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_private_marker_errors(item, f"{label}[{index}]"))
    return errors


def _contains_private_marker(value: Any) -> bool:
    """Detect markers in content values without treating metadata keys as content."""
    if isinstance(value, dict):
        return any(_contains_private_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        return any(marker.casefold() in lowered for marker in PACKAGE_PRIVATE_CONTENT_MARKERS)
    return False


def _validate_task(task: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    if task.get("schema_version") != GENERATION_TASK_SCHEMA_VERSION:
        errors.append(f"{label} has the wrong generation-task schema")
    for field in ("task_id", "source_id", "source_dataset", "design_family", "language", "specification", "provenance"):
        if field not in task:
            errors.append(f"{label} is missing {field}")
    if task.get("language") != "systemverilog":
        errors.append(f"{label}.language must be systemverilog")
    if not isinstance(task.get("specification"), str):
        errors.append(f"{label}.specification must be a string")
    if not isinstance(task.get("provenance"), dict):
        errors.append(f"{label}.provenance must be an object")
    errors.extend(_private_marker_errors(task, label))
    return sorted(set(errors))


def _validate_candidate_record(record: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "candidate_id", "task_id", "source_id", "attempt",
        "packet_id", "candidate_sha256", "candidate",
    }
    if set(record) != required:
        errors.append(f"{label} has an invalid field set")
    if record.get("schema_version") != "rtl_teacher_candidate_record_v0.1":
        errors.append(f"{label} has the wrong schema")
    if type(record.get("attempt")) is not int or not 1 <= record.get("attempt", 0) <= 4:
        errors.append(f"{label}.attempt is invalid")
    candidate = record.get("candidate")
    if not isinstance(candidate, dict):
        return errors + [f"{label}.candidate must be an object"]
    candidate_required = {
        "schema_version", "task_id", "top_module", "language", "rtl",
        "implementation_summary", "assumptions",
    }
    if set(candidate) != candidate_required:
        errors.append(f"{label}.candidate has an invalid field set")
    if candidate.get("schema_version") != "rtl_teacher_candidate_v0.1":
        errors.append(f"{label}.candidate has the wrong schema")
    if not isinstance(candidate.get("rtl"), str) or not candidate.get("rtl", "").strip():
        errors.append(f"{label}.candidate.rtl must be non-empty text")
    else:
        actual_hash = _sha256_bytes(candidate["rtl"].encode("utf-8"))
        if record.get("candidate_sha256") != actual_hash:
            errors.append(f"{label}.candidate_sha256 does not match candidate RTL")
    if not isinstance(record.get("candidate_sha256"), str) or not SHA256_RE.fullmatch(record.get("candidate_sha256", "")):
        errors.append(f"{label}.candidate_sha256 is invalid")
    if record.get("candidate_id") != f"{record.get('task_id')}_attempt_{record.get('attempt', 0):02d}":
        errors.append(f"{label}.candidate_id is not deterministic")
    errors.extend(_private_marker_errors(candidate, f"{label}.candidate"))
    return sorted(set(errors))


def _validate_diagnostics(diagnostics: Any, label: str) -> tuple[list[str], int]:
    """Validate the only diagnostics permitted on an accepted attempt.

    RTLBench may emit a sanitized zero-exit compiler warning.  Raw output,
    private paths, and arbitrary diagnostics are never accepted into the
    packaging gate.
    """
    errors: list[str] = []
    if not isinstance(diagnostics, list):
        return [f"{label} must be an array"], 0
    warning_count = 0
    for index, value in enumerate(diagnostics):
        if not isinstance(value, str):
            errors.append(f"{label}[{index}] must be text")
            continue
        errors.extend(_private_marker_errors(value, f"{label}[{index}]"))
        if not SANITIZED_COMPILE_WARNING_RE.fullmatch(value):
            errors.append(f"{label}[{index}] is not a sanitized compile warning")
        else:
            warning_count += 1
    return sorted(set(errors)), warning_count


def _warning_metadata(attempt: dict[str, Any]) -> dict[str, Any]:
    diagnostics = attempt.get("diagnostics")
    warning_count = len(diagnostics) if isinstance(diagnostics, list) else 0
    return {
        "warnings_present": warning_count > 0,
        "warning_count": warning_count,
        "acceptance_affected": False,
    }


def _validate_attempt_row(attempt: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    if attempt.get("schema_version") != ATTEMPT_SCHEMA_VERSION:
        errors.append(f"{label} has the wrong attempt schema")
    if attempt.get("verification_profile") != PROFILE:
        errors.append(f"{label} has the wrong verification profile")
    for field in ("candidate_id", "task_id", "source_id", "top_module", "candidate_sha256", "failure_category", "checks", "mismatch_summary", "diagnostics", "toolchain"):
        if field not in attempt:
            errors.append(f"{label} is missing {field}")
    try:
        _validate_checks(attempt.get("checks"), REQUESTED_CHECKS)
        _validate_mismatch(attempt.get("mismatch_summary"))
        _validate_toolchain(attempt.get("toolchain"), f"{label}.toolchain")
    except (WorkflowError, TypeError, KeyError, ValueError) as exc:
        errors.append(f"{label} verification fields are invalid: {exc}")
    if attempt.get("accepted") is not True:
        errors.append(f"{label} is not accepted")
    if attempt.get("failure_category") != "passed":
        errors.append(f"{label} is not a passed attempt")
    checks = attempt.get("checks") if isinstance(attempt.get("checks"), dict) else {}
    compile_status = checks.get("compile", {}).get("candidate", {})
    simulation_status = checks.get("simulation", {}).get("candidate_passes", {})
    if compile_status.get("passed") is not True:
        errors.append(f"{label} compile did not pass")
    if simulation_status.get("passed") is not True:
        errors.append(f"{label} simulation did not pass")
    mismatch = attempt.get("mismatch_summary") if isinstance(attempt.get("mismatch_summary"), dict) else {}
    if mismatch.get("maximum_count") != 0 or any(count != 0 for count in mismatch.get("reported_counts", [])):
        errors.append(f"{label} has non-zero mismatches")
    if mismatch.get("timeout_reported") is not False:
        errors.append(f"{label} reports a timeout")
    diagnostic_errors, _ = _validate_diagnostics(attempt.get("diagnostics"), f"{label}.diagnostics")
    errors.extend(diagnostic_errors)
    if not isinstance(attempt.get("candidate_sha256"), str) or not SHA256_RE.fullmatch(attempt.get("candidate_sha256", "")):
        errors.append(f"{label}.candidate_sha256 is invalid")
    return sorted(set(errors))


def _validate_package_attempt_row(attempt: dict[str, Any], label: str) -> list[str]:
    """Validate an attempt input while permitting excluded candidate failures."""
    if attempt.get("accepted") is True:
        return _validate_attempt_row(attempt, label)
    errors: list[str] = []
    if attempt.get("schema_version") != ATTEMPT_SCHEMA_VERSION:
        errors.append(f"{label} has the wrong attempt schema")
    if attempt.get("verification_profile") != PROFILE:
        errors.append(f"{label} has the wrong verification profile")
    for field in (
        "candidate_id", "task_id", "source_id", "top_module", "attempt",
        "candidate_sha256", "failure_category", "checks", "mismatch_summary",
        "diagnostics", "toolchain",
    ):
        if field not in attempt:
            errors.append(f"{label} is missing {field}")
    if attempt.get("failure_category") in {None, "passed"}:
        errors.append(f"{label} has no failure category")
    if not isinstance(attempt.get("diagnostics"), list) or not all(isinstance(item, str) for item in attempt.get("diagnostics", [])):
        errors.append(f"{label}.diagnostics is invalid")
    else:
        errors.extend(_private_marker_errors(attempt["diagnostics"], f"{label}.diagnostics"))
    try:
        _validate_checks(attempt.get("checks"), REQUESTED_CHECKS)
        _validate_mismatch(attempt.get("mismatch_summary"))
        _validate_toolchain(attempt.get("toolchain"), f"{label}.toolchain")
    except (WorkflowError, TypeError, KeyError, ValueError) as exc:
        errors.append(f"{label} verification fields are invalid: {exc}")
    if not isinstance(attempt.get("candidate_sha256"), str) or not SHA256_RE.fullmatch(attempt.get("candidate_sha256", "")):
        errors.append(f"{label}.candidate_sha256 is invalid")
    if attempt.get("candidate_id") != f"{attempt.get('task_id')}_attempt_{attempt.get('attempt', 0):02d}":
        errors.append(f"{label}.candidate_id is not deterministic")
    return sorted(set(errors))


def _validate_evidence_row(evidence: dict[str, Any], attempt: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append(f"{label} has the wrong evidence schema")
    for field in ("candidate_id", "task_id", "source_id", "attempt", "top_module", "checks", "mismatch_summary", "accepted", "failure_category", "input_hashes"):
        if field not in evidence:
            errors.append(f"{label} is missing {field}")
    for field in ("candidate_id", "task_id", "source_id", "attempt", "top_module", "accepted", "failure_category", "checks", "mismatch_summary", "diagnostics", "toolchain"):
        if field in evidence and field in attempt and evidence[field] != attempt[field]:
            errors.append(f"{label}.{field} disagrees with attempt history")
    errors.extend(_private_marker_errors(evidence.get("diagnostics", []), f"{label}.diagnostics"))
    return sorted(set(errors))


def _validate_sidecar(
    sidecar: dict[str, Any],
    evidence_path: Path | None,
    expected_commit: str | None,
    expected_profile: str | None = None,
    expected_image: str | None = None,
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "profile", "runtime", "runtime_mode", "rootless",
        "image_identity_kind", "image", "image_id", "image_digest",
        "rtlbench_commit", "runner_config_version", "manifest_sha256",
        "workspace_tree_sha256", "evidence_sha256", "partial_evidence_sha256",
    }
    if not required.issubset(sidecar):
        errors.append("runner sidecar is missing identity fields")
    if sidecar.get("schema_version") != "rtlbench_runner_identity_v0.2":
        errors.append("runner sidecar has the wrong schema")
    if sidecar.get("runtime") not in {"docker", "podman"}:
        errors.append("runner sidecar runtime is invalid")
    if expected_profile is not None and sidecar.get("profile") != expected_profile:
        errors.append("runner sidecar profile differs from expected")
    if not isinstance(sidecar.get("image_id"), str) or not sidecar.get("image_id", "").startswith("sha256:"):
        errors.append("runner sidecar image identity is not immutable")
    if sidecar.get("image") != sidecar.get("image_id"):
        errors.append("runner sidecar image and image_id differ")
    if expected_image is not None and sidecar.get("image_id") != expected_image:
        errors.append("runner sidecar image differs from expected")
    if expected_commit is not None and sidecar.get("rtlbench_commit") != expected_commit:
        errors.append("runner sidecar RTLBench commit differs from expected")
    if sidecar.get("partial_evidence_sha256") is not None:
        errors.append("runner sidecar references partial evidence")
    for field in ("manifest_sha256", "workspace_tree_sha256", "evidence_sha256"):
        if not isinstance(sidecar.get(field), str) or not SHA256_RE.fullmatch(sidecar.get(field, "")):
            errors.append(f"runner sidecar {field} is invalid")
    if evidence_path is not None and evidence_path.exists():
        if sidecar.get("evidence_sha256") != _sha256_file(evidence_path):
            errors.append("runner sidecar evidence hash mismatch")
    return sorted(set(errors))


def _evidence_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(
            item
            for item in path.rglob("candidate_evidence.jsonl")
            if item.is_file() and not item.is_symlink()
        )
    return [path]


def _sidecar_files(path: Path | None) -> list[Path]:
    if path is None:
        return []
    if path.is_dir():
        return sorted(
            item
            for item in path.rglob("*.runner.json")
            if item.is_file() and not item.is_symlink()
        )
    return [path]


def _load_evidence_bundle(
    evidence_path: Path | None,
    sidecar_path: Path | None,
    *,
    expected_commit: str | None,
    expected_profile: str | None = None,
    expected_image: str | None = None,
    strict: bool,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, dict[str, Any]], list[str]]:
    """Load evidence files and map runner identities to their candidate rows."""
    if evidence_path is None:
        return [], {}, {}, ["evidence input is required"] if strict else []
    errors: list[str] = []
    files = _evidence_files(evidence_path)
    if not files:
        errors.append("evidence input contains no candidate_evidence.jsonl files")
        return [], {}, {}, errors
    explicit_sidecars = _sidecar_files(sidecar_path)
    sidecars_by_hash: dict[str, dict[str, Any]] = {}
    sidecar_sources: dict[str, Path] = {}
    for path in explicit_sidecars:
        try:
            value = _load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
            errors.append(f"invalid runner sidecar {path.name}: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"runner sidecar is not an object: {path.name}")
            continue
        errors.extend(_validate_sidecar(value, None, expected_commit, expected_profile, expected_image))
        evidence_hash = value.get("evidence_sha256")
        if isinstance(evidence_hash, str):
            sidecars_by_hash[evidence_hash] = value
            sidecar_sources[evidence_hash] = path

    evidence_rows: list[dict[str, Any]] = []
    evidence_hash_by_candidate: dict[str, str] = {}
    sidecar_by_candidate: dict[str, dict[str, Any]] = {}
    for path in files:
        try:
            rows = _load_jsonl(path)
            evidence_hash = _sha256_file(path)
        except WorkflowError as exc:
            errors.append(str(exc))
            continue
        sibling = path.with_name(path.name + ".runner.json")
        sidecar: dict[str, Any] | None = None
        if sibling.is_file() and not sibling.is_symlink():
            try:
                sibling_value = _load_json(sibling)
            except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
                errors.append(f"invalid runner sidecar {sibling.name}: {exc}")
            else:
                if isinstance(sibling_value, dict):
                    errors.extend(
                        _validate_sidecar(
                            sibling_value,
                            path,
                            expected_commit,
                            expected_profile,
                            expected_image,
                        )
                    )
                    sidecar = sibling_value
                else:
                    errors.append(f"runner sidecar is not an object: {sibling.name}")
        if sidecar is None:
            sidecar = sidecars_by_hash.get(evidence_hash)
        if strict and sidecar is None:
            errors.append(f"no runner sidecar matches evidence file: {path.name}")
        for row in rows:
            candidate_id = row.get("candidate_id")
            if not isinstance(candidate_id, str):
                errors.append(f"evidence row in {path.name} has no candidate_id")
                continue
            if candidate_id in evidence_hash_by_candidate:
                errors.append(f"duplicate evidence candidate_id: {candidate_id}")
            evidence_hash_by_candidate[candidate_id] = evidence_hash
            if sidecar is not None:
                sidecar_by_candidate[candidate_id] = sidecar
            evidence_rows.append(row)
    return evidence_rows, evidence_hash_by_candidate, sidecar_by_candidate, sorted(set(errors))


def _load_split(split_path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    value = _load_json(split_path)
    errors: list[str] = []
    if value.get("schema_version") != SPLIT_SCHEMA_VERSION:
        errors.append("wrong split schema")
    rows = value.get("rows")
    if not isinstance(rows, list):
        errors.append("split rows must be an array")
        rows = []
    by_task: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("task_id"), str):
            errors.append("split row has no task_id")
            continue
        if row["task_id"] in by_task:
            errors.append(f"duplicate split task_id: {row['task_id']}")
        by_task[row["task_id"]] = row
    return by_task, sorted(set(errors))


def _validate_smoke_provenance(
    *,
    tasks: list[dict[str, Any]],
    split_path: Path,
    split_by_task: dict[str, dict[str, Any]],
    base_split_path: Path | None,
    source_acquisition_path: Path | None,
    asset_correction_report_path: Path | None,
    qualified_correction_manifest_path: Path | None,
    asset_qualification_path: Path | None,
    qualification_binding_path: Path | None,
    expected_source_commit: str,
    expected_source_tree_sha256: str,
    expected_frozen_split_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    """Validate the complete five-row assetfix provenance chain."""
    errors: list[str] = []
    required_paths = {
        "base split manifest": base_split_path,
        "source acquisition attestation": source_acquisition_path,
        "assetfix report": asset_correction_report_path,
        "qualified correction manifest": qualified_correction_manifest_path,
        "asset qualification report": asset_qualification_path,
        "qualification binding": qualification_binding_path,
    }
    for label, path in required_paths.items():
        if path is None:
            errors.append(f"{label} is required for smoke packaging")
        elif path.is_symlink() or not path.is_file():
            errors.append(f"{label} is missing or symlinked")
    if errors:
        return {}, sorted(set(errors))

    assert base_split_path is not None
    assert source_acquisition_path is not None
    assert asset_correction_report_path is not None
    assert qualified_correction_manifest_path is not None
    assert asset_qualification_path is not None
    assert qualification_binding_path is not None

    try:
        base_split = _load_json(base_split_path)
        readiness_split = _load_json(split_path)
        acquisition = _load_json(source_acquisition_path)
        assetfix_report = _load_json(asset_correction_report_path)
        qualified_rows = _load_jsonl(qualified_correction_manifest_path)
        qualification_report = _load_json(asset_qualification_path)
        binding = _load_json(qualification_binding_path)
    except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
        return {}, [f"could not load smoke provenance: {exc}"]

    if not isinstance(base_split, dict):
        errors.append("base frozen split must be an object")
        base_split = {}
    if not isinstance(readiness_split, dict):
        errors.append("readiness split must be an object")
        readiness_split = {}
    if not isinstance(acquisition, dict):
        errors.append("source acquisition must be an object")
        acquisition = {}
    if not isinstance(assetfix_report, dict):
        errors.append("assetfix report must be an object")
        assetfix_report = {}
    if not isinstance(qualification_report, dict):
        errors.append("qualification report must be an object")
        qualification_report = {}
    if not isinstance(binding, dict):
        errors.append("qualification binding must be an object")
        binding = {}

    base_split_sha256 = _sha256_file(base_split_path)
    readiness_split_sha256 = _sha256_file(split_path)
    acquisition_sha256 = _sha256_file(source_acquisition_path)
    assetfix_report_sha256 = _sha256_file(asset_correction_report_path)
    qualified_manifest_sha256 = _sha256_file(qualified_correction_manifest_path)
    qualification_report_sha256 = _sha256_file(asset_qualification_path)
    qualification_binding_sha256 = _sha256_file(qualification_binding_path)

    if base_split_sha256 != expected_frozen_split_sha256:
        errors.append("base frozen split hash mismatch")
    if base_split.get("schema_version") != SPLIT_SCHEMA_VERSION:
        errors.append("base frozen split has the wrong schema")
    if base_split.get("row_count") != 156 or base_split.get("counts") != {"train": 111, "validation": 23, "test": 22}:
        errors.append("base frozen split counts are not the pinned 156-row split")
    if readiness_split.get("base_split_sha256") != expected_frozen_split_sha256:
        errors.append("readiness split is not bound to the frozen split")
    if readiness_split.get("schema_version") != SPLIT_SCHEMA_VERSION:
        errors.append("readiness split has the wrong schema")

    if acquisition.get("schema_version") != "rtl_source_acquisition_v0.1":
        errors.append("source acquisition has the wrong schema")
    if acquisition.get("repository") != "NVlabs/verilog-eval":
        errors.append("source acquisition repository identity mismatch")
    if acquisition.get("source_commit") != expected_source_commit:
        errors.append("source acquisition commit mismatch")
    if acquisition.get("source_tree_sha256") != expected_source_tree_sha256:
        errors.append("source acquisition tree hash mismatch")
    if acquisition.get("row_count") != 156 or acquisition.get("dirty_checkout") is not False or acquisition.get("symlink_count") != 0:
        errors.append("source acquisition cleanliness or row count mismatch")

    if assetfix_report.get("schema_version") != "rtl_verification_asset_correction_v0.1":
        errors.append("assetfix report has the wrong schema")
    for field, expected in (
        ("source_commit", expected_source_commit),
        ("source_tree_sha256", expected_source_tree_sha256),
        ("base_split_sha256", expected_frozen_split_sha256),
        ("correction_version", SMOKE_CORRECTION_VERSION),
    ):
        if assetfix_report.get(field) != expected:
            errors.append(f"assetfix report {field} mismatch")
    if assetfix_report.get("row_count") != 5 or assetfix_report.get("dependency_closure_passed") is not True or assetfix_report.get("reference_copied_to_support") is not False:
        errors.append("assetfix report does not describe the passed private-asset boundary")

    expected_source_ids = list(SMOKE_SOURCE_IDS)
    expected_task_ids = list(SMOKE_TASK_IDS)
    errors.extend(_validate_smoke_task_order(tasks))
    if len(qualified_rows) != 5 or {row.get("source_id") for row in qualified_rows} != set(expected_source_ids):
        errors.append("qualified correction manifest does not cover the five smoke sources")
    qualified_by_source = {row.get("source_id"): row for row in qualified_rows}
    for source_id in expected_source_ids:
        row = qualified_by_source.get(source_id)
        if row is None:
            continue
        if row.get("correction_version") != SMOKE_CORRECTION_VERSION or row.get("dependency_closure") != "passed" or row.get("support_files") != [] or row.get("reference_copied_to_support") is not False:
            errors.append(f"qualified correction row is unsafe: {source_id}")

    report_rows = qualification_report.get("rows") if isinstance(qualification_report, dict) else None
    if qualification_report.get("qualification_passed") is not True or qualification_report.get("row_count") != 5 or qualification_report.get("mutation_count") != 10:
        errors.append("qualification report is not the passed ten-mutation retry")
    if qualification_report.get("correction_manifest_sha256") != qualified_manifest_sha256:
        errors.append("qualification report correction-manifest hash mismatch")
    if not isinstance(report_rows, list) or {row.get("source_id") for row in report_rows} != set(expected_source_ids):
        errors.append("qualification report does not cover the five smoke sources")

    if binding.get("schema_version") != "rtl_generation_qualification_binding_v0.1":
        errors.append("qualification binding has the wrong schema")
    for field, expected in (
        ("source_commit", expected_source_commit),
        ("source_tree_sha256", expected_source_tree_sha256),
        ("frozen_split_sha256", expected_frozen_split_sha256),
        ("correction_version", SMOKE_CORRECTION_VERSION),
        ("qualification_result", "passed"),
        ("reference_supplied", False),
        ("mutation_rows", 10),
        ("detected_mutations", 10),
    ):
        if binding.get(field) != expected:
            errors.append(f"qualification binding {field} mismatch")
    if binding.get("qualification_report_sha256") != qualification_report_sha256:
        errors.append("qualification binding report hash mismatch")
    if binding.get("correction_manifest_sha256") != qualified_manifest_sha256:
        errors.append("qualification binding correction-manifest hash mismatch")
    binding_rows = binding.get("rows") if isinstance(binding, dict) else None
    if not isinstance(binding_rows, list) or {row.get("source_id") for row in binding_rows} != set(expected_source_ids):
        errors.append("qualification binding does not cover the five smoke sources")
    binding_by_source = {row.get("source_id"): row for row in binding_rows or []}
    for source_id in expected_source_ids:
        binding_row = binding_by_source.get(source_id)
        qualified_row = qualified_by_source.get(source_id)
        if binding_row is None or qualified_row is None:
            continue
        if binding_row.get("qualification_result") != "passed":
            errors.append(f"qualification row is not passed: {source_id}")
        if binding_row.get("corrected_testbench_sha256") != qualified_row.get("corrected_testbench_sha256"):
            errors.append(f"qualified testbench hash mismatch: {source_id}")

    base_rows = assetfix_report.get("rows") if isinstance(assetfix_report, dict) else None
    base_by_source = {row.get("source_id"): row for row in base_rows or []}
    if set(base_by_source) != set(expected_source_ids):
        errors.append("assetfix report does not cover the five smoke sources")
    for source_id in expected_source_ids:
        base_row = base_by_source.get(source_id)
        qualified_row = qualified_by_source.get(source_id)
        if base_row is None or qualified_row is None:
            continue
        for field in ("original_prompt_sha256", "original_reference_rtl_sha256", "original_testbench_sha256", "task_id", "upstream_commit"):
            if base_row.get(field) != qualified_row.get(field):
                errors.append(f"assetfix lineage mismatch for {source_id}: {field}")

    split_train_ids = readiness_split.get("splits", {}).get("train", [])
    for task, source_id, task_id in zip(tasks, expected_source_ids, expected_task_ids):
        split_row = split_by_task.get(task_id)
        if source_id not in split_train_ids or split_row is None or split_row.get("split") != "train" or split_row.get("verification_readiness") != "executable_ready":
            errors.append(f"task is not an executable-ready frozen training row: {task_id}")

    context = {
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "frozen_split_sha256": expected_frozen_split_sha256,
        "readiness_split_sha256": readiness_split_sha256,
        "source_acquisition_sha256": acquisition_sha256,
        "assetfix_report_sha256": assetfix_report_sha256,
        "qualified_correction_manifest_sha256": qualified_manifest_sha256,
        "qualification_report_sha256": qualification_report_sha256,
        "qualification_binding_sha256": qualification_binding_sha256,
        "correction_version": SMOKE_CORRECTION_VERSION,
        "qualification_source": binding.get("qualification_source"),
        "mutation_rows": binding.get("mutation_rows"),
        "detected_mutations": binding.get("detected_mutations"),
        "reference_supplied": binding.get("reference_supplied"),
        "qualified_by_source": qualified_by_source,
        "binding_by_source": binding_by_source,
    }
    return context, sorted(set(errors))


def _normalized_rtl_hash(rtl: str) -> str:
    without_comments = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", rtl, flags=re.DOTALL)
    return _sha256_bytes(re.sub(r"\s+", " ", without_comments).strip().encode("utf-8"))


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise WorkflowError(f"output must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "all": output_dir / "all.jsonl",
        "train": output_dir / "train.jsonl",
        "rejected_rows": output_dir / "rejected_rows.jsonl",
        "manifest": output_dir / "manifest.json",
        "statistics": output_dir / "statistics.json",
        "dataset_card": output_dir / "dataset_card.md",
        "validation_report": output_dir / "validation_report.json",
        "validation_report_md": output_dir / "validation_report.md",
        "provenance_report": output_dir / "provenance_report.json",
    }


def _check_output_dir(output_dir: Path, force: bool) -> list[str]:
    errors: list[str] = []
    if output_dir.is_symlink():
        return ["output directory must not be a symlink"]
    if output_dir.exists() and not output_dir.is_dir():
        return ["output path must be a directory"]
    managed = _output_paths(output_dir)
    for path in managed.values():
        if path.exists() and path.is_symlink():
            errors.append(f"managed output must not be a symlink: {path.name}")
        elif path.exists() and not force:
            errors.append(f"managed output already exists: {path.name}; use --force")
    return errors


def _load_recovery_authorization(
    path: Path | None,
    *,
    expected_package_id: str | None,
    expected_parent_package_id: str | None,
    expected_recovery_reason: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, ["recovery authorization is required"]
    try:
        authorization = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
        return None, [f"invalid recovery authorization: {exc}"]
    if not isinstance(authorization, dict):
        return None, ["recovery authorization must be an object"]
    errors: list[str] = []
    if authorization.get("schema_version") != RECOVERY_AUTHORIZATION_SCHEMA_VERSION:
        errors.append("recovery authorization has the wrong schema")
    authorization_status = authorization.get("authorization_status", authorization.get("status"))
    if authorization_status not in {"one_recovery_invocation_authorized", "authorized_once"}:
        errors.append("recovery authorization is not active")
    original_package_id = authorization.get("original_package_id", authorization.get("original_package"))
    if original_package_id != RECOVERY_PARENT_PACKAGE_ID:
        errors.append("recovery authorization parent package mismatch")
    parent_package_id = authorization.get("parent_failed_package")
    if parent_package_id is not None and parent_package_id != expected_parent_package_id:
        errors.append("recovery authorization failed-parent package mismatch")
    original_status = authorization.get("original_status", authorization.get("parent_status"))
    if original_status != "failed_partial_package":
        errors.append("recovery authorization original status mismatch")
    original_consumable = authorization.get("original_consumable")
    if original_consumable is not False:
        errors.append("recovery authorization original package is not non-consumable")
    recovery_package = authorization.get("recovery_package", authorization.get("package_id"))
    if recovery_package != expected_package_id:
        errors.append("recovery authorization recovery package mismatch")
    recovery_reason = authorization.get("recovery_reason", authorization.get("failure_category"))
    if recovery_reason != expected_recovery_reason:
        errors.append("recovery authorization reason mismatch")
    source_defects_fixed = authorization.get("source_defects_fixed", authorization.get("source_fix_completed"))
    if source_defects_fixed is not True:
        errors.append("recovery authorization does not confirm source fixes")
    regression_tests_passed = authorization.get("regression_tests_passed", authorization.get("focused_regression_tests_passed"))
    if regression_tests_passed is not True:
        errors.append("recovery authorization does not confirm regression tests")
    synthetic_end_to_end_passed = authorization.get("synthetic_end_to_end_passed", authorization.get("nonpublication_end_to_end_preflight_passed"))
    if synthetic_end_to_end_passed is not True:
        errors.append("recovery authorization does not confirm synthetic end-to-end validation")
    canonical_inputs_preflighted = authorization.get("canonical_inputs_preflighted", authorization.get("canonical_inputs_changed") is False)
    if canonical_inputs_preflighted is not True:
        errors.append("recovery authorization does not confirm canonical input preflight")
    original_partial_package_preserved = authorization.get("original_partial_package_preserved", authorization.get("previous_outputs_preserved"))
    if original_partial_package_preserved is not True:
        errors.append("recovery authorization does not confirm partial-package preservation")
    authorized_invocation_count = authorization.get("authorized_invocation_count", authorization.get("authorized_invocations"))
    if authorized_invocation_count != 1:
        errors.append("recovery authorization must authorize exactly one invocation")
    return authorization, sorted(set(errors))


def _lineage_errors(
    manifest: dict[str, Any],
    *,
    expected_package_id: str | None,
    expected_parent_package_id: str | None,
    expected_recovery_reason: str | None,
    require_recovery_lineage: bool,
    require_consumable: bool,
) -> list[str]:
    if not require_recovery_lineage:
        return []
    errors: list[str] = []
    lineage = manifest.get("lineage")
    if not isinstance(lineage, dict):
        return ["package lineage is required"]
    if lineage.get("schema_version") != PACKAGE_LINEAGE_SCHEMA_VERSION:
        errors.append("package lineage has the wrong schema")
    if lineage.get("package_id") != expected_package_id:
        errors.append("package lineage package ID mismatch")
    if lineage.get("parent_package_id") != expected_parent_package_id:
        errors.append("package lineage parent package mismatch")
    if lineage.get("recovery_reason") != expected_recovery_reason:
        errors.append("package lineage recovery reason mismatch")
    status = lineage.get("status")
    consumable = lineage.get("consumable")
    if status not in {"pending_validation", "pending_finalization", "failed_partial_package", "successful_recovery_package"}:
        errors.append("package lineage status is invalid")
    if not isinstance(consumable, bool):
        errors.append("package lineage consumable flag is invalid")
    if require_consumable and (status != "successful_recovery_package" or consumable is not True):
        errors.append("package is not marked consumable after full validation")
    if status == "successful_recovery_package" and consumable is not True:
        errors.append("successful recovery package must be consumable")
    if status == "pending_finalization" and consumable is not False:
        errors.append("pending package must not be consumable")
    if status == "failed_partial_package" and consumable is not False:
        errors.append("failed partial package must not be consumable")
    return sorted(set(errors))


def _package_task_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover public task objects from the generation SFT message envelope."""
    tasks: list[dict[str, Any]] = []
    for row in rows:
        messages = row.get("messages")
        if not isinstance(messages, list) or len(messages) <= 1:
            continue
        user_message = messages[1]
        if not isinstance(user_message, dict):
            continue
        task = user_message.get("content")
        if isinstance(task, dict):
            tasks.append(task)
    return tasks


def _validate_smoke_task_order(tasks: list[dict[str, Any]]) -> list[str]:
    if (
        len(tasks) != len(SMOKE_TASK_IDS)
        or [task.get("source_id") for task in tasks] != list(SMOKE_SOURCE_IDS)
        or [task.get("task_id") for task in tasks] != list(SMOKE_TASK_IDS)
    ):
        return ["tasks do not match the pinned five-row smoke order"]
    return []


def _validate_qualified_subset_provenance(
    *,
    tasks: list[dict[str, Any]],
    split_path: Path,
    base_split_path: Path | None,
    source_acquisition_path: Path | None,
    asset_qualification_path: Path,
    qualification_binding_path: Path,
    qualified_correction_manifest_path: Path,
    expected_source_commit: str,
    expected_source_tree_sha256: str,
    expected_frozen_split_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    """Validate a passed subset of a larger qualification run.

    Batch qualification reports can cover more tasks than the generation
    package.  The qualified-subset binding is the authority for which rows
    are eligible; the original qualification report is retained as the
    evidence source and is never rewritten.
    """
    errors: list[str] = []
    base_split: dict[str, Any] | None = None
    acquisition: dict[str, Any] | None = None
    try:
        report = _load_json(asset_qualification_path)
        binding = _load_json(qualification_binding_path)
        qualified_rows = _load_jsonl(qualified_correction_manifest_path)
        split_by_task, split_errors = _load_split(split_path)
        errors.extend(split_errors)
        base_split = _load_json(base_split_path) if base_split_path is not None else None
        acquisition = _load_json(source_acquisition_path) if source_acquisition_path is not None else None
    except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
        return {}, [f"could not load qualified-subset provenance: {exc}"]

    if not isinstance(report, dict) or report.get("schema_version") != QUALIFIED_SUBSET_REPORT_SCHEMA:
        errors.append("qualified-subset qualification report has the wrong schema")
        report = {}
    if not isinstance(binding, dict) or binding.get("schema_version") != QUALIFIED_SUBSET_BINDING_SCHEMA:
        errors.append("qualified-subset binding has the wrong schema")
        binding = {}
    if not isinstance(base_split, (dict, type(None))):
        errors.append("base split must be an object")
        base_split = None
    if not isinstance(acquisition, (dict, type(None))):
        errors.append("source acquisition must be an object")
        acquisition = None

    if base_split_path is not None:
        if _sha256_file(base_split_path) != expected_frozen_split_sha256:
            errors.append("base frozen split hash mismatch")
        if not isinstance(base_split, dict) or base_split.get("row_count") != 156 or base_split.get("counts") != {"train": 111, "validation": 23, "test": 22}:
            errors.append("base frozen split counts are not the pinned 156-row split")
    if source_acquisition_path is not None:
        if not isinstance(acquisition, dict):
            errors.append("source acquisition is missing")
        else:
            if acquisition.get("schema_version") != "rtl_source_acquisition_v0.1":
                errors.append("source acquisition has the wrong schema")
            if acquisition.get("repository") != "NVlabs/verilog-eval":
                errors.append("source acquisition repository identity mismatch")
            if acquisition.get("source_commit") != expected_source_commit:
                errors.append("source acquisition commit mismatch")
            if acquisition.get("source_tree_sha256") != expected_source_tree_sha256:
                errors.append("source acquisition tree hash mismatch")
            if acquisition.get("row_count") != 156 or acquisition.get("dirty_checkout") is not False or acquisition.get("symlink_count") != 0:
                errors.append("source acquisition cleanliness or row count mismatch")

    for field, expected in (
        ("source_commit", expected_source_commit),
        ("source_tree_sha256", expected_source_tree_sha256),
        ("frozen_split_sha256", expected_frozen_split_sha256),
        ("correction_version", QUALIFIED_SUBSET_CORRECTION_VERSION),
    ):
        if binding.get(field) != expected:
            errors.append(f"qualified-subset binding {field} mismatch")
    if binding.get("qualification_result") not in {None, "passed"}:
        errors.append("qualified-subset binding is not passed")
    if binding.get("qualification_validator_authoritative") is not True:
        errors.append("qualified-subset binding is not authoritative")
    if binding.get("reference_rtl_supplied") is not False:
        errors.append("qualified-subset binding supplied reference RTL")
    if binding.get("support_file_count") != 0:
        errors.append("qualified-subset binding contains support files")
    if binding.get("qualification_report_sha256") != _sha256_file(asset_qualification_path):
        errors.append("qualified-subset binding qualification-report hash mismatch")
    if binding.get("qualified_correction_manifest_sha256") != _sha256_file(qualified_correction_manifest_path):
        errors.append("qualified-subset binding correction-manifest hash mismatch")

    report_rows = report.get("rows") if isinstance(report, dict) else None
    report_by_source = {
        row.get("source_id"): row
        for row in report_rows or []
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }
    binding_rows = binding.get("rows") if isinstance(binding, dict) else None
    binding_by_source = {
        row.get("source_id"): row
        for row in binding_rows or []
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }
    correction_by_source = {
        row.get("source_id"): row
        for row in qualified_rows
        if isinstance(row, dict) and isinstance(row.get("source_id"), str)
    }
    qualified_source_ids = [row.get("source_id") for row in binding_rows or []]
    if len(qualified_source_ids) != len(set(qualified_source_ids)):
        errors.append("qualified-subset binding contains duplicate source IDs")
    if binding.get("qualified_task_count") != len(qualified_source_ids):
        errors.append("qualified-subset binding task count mismatch")
    if len(qualified_rows) != len(qualified_source_ids) or set(correction_by_source) != set(qualified_source_ids):
        errors.append("qualified correction manifest does not match the passed subset")

    task_by_source = {task.get("source_id"): task for task in tasks}
    if len(task_by_source) != len(tasks):
        errors.append("package tasks contain duplicate source IDs")
    for task in tasks:
        source_id = task.get("source_id")
        task_id = task.get("task_id")
        binding_row = binding_by_source.get(source_id)
        report_row = report_by_source.get(source_id)
        correction_row = correction_by_source.get(source_id)
        if binding_row is None:
            errors.append(f"task is absent from qualified subset: {task_id}")
            continue
        if binding_row.get("task_id") != task_id or binding_row.get("qualification_result") != "passed":
            errors.append(f"qualified-subset task identity or result mismatch: {task_id}")
        if not isinstance(report_row, dict) or report_row.get("qualification_passed") is not True:
            errors.append(f"task does not have a passed qualification row: {task_id}")
        if not isinstance(correction_row, dict):
            errors.append(f"task is absent from qualified correction manifest: {task_id}")
        else:
            if correction_row.get("task_id") != task_id or correction_row.get("split") != "train":
                errors.append(f"qualified correction identity or split mismatch: {task_id}")
            if correction_row.get("correction_version") != QUALIFIED_SUBSET_CORRECTION_VERSION:
                errors.append(f"qualified correction version mismatch: {task_id}")
            if correction_row.get("dependency_closure") != "passed" or correction_row.get("verification_readiness") != "executable_ready":
                errors.append(f"qualified correction is not executable-ready: {task_id}")
            if correction_row.get("support_files") != [] or correction_row.get("reference_copied_to_support") is not False:
                errors.append(f"qualified correction privacy contract failed: {task_id}")
            if binding_row.get("corrected_testbench_sha256") != correction_row.get("corrected_testbench_sha256"):
                errors.append(f"corrected testbench hash mismatch: {task_id}")

        split_row = split_by_task.get(task_id)
        if split_row is None or split_row.get("split") != "train":
            errors.append(f"task is not in the frozen train split: {task_id}")
        if split_row is not None and split_row.get("verification_readiness") not in {"executable_ready", "needs_testbench"}:
            errors.append(f"task has invalid split readiness: {task_id}")

    qualification_report_hash = _sha256_file(asset_qualification_path)
    qualification_binding_hash = _sha256_file(qualification_binding_path)
    context = {
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "frozen_split_sha256": expected_frozen_split_sha256,
        "readiness_split_sha256": _sha256_file(split_path),
        "source_acquisition_sha256": _sha256_file(source_acquisition_path) if source_acquisition_path is not None else None,
        "qualified_correction_manifest_sha256": _sha256_file(qualified_correction_manifest_path),
        "qualification_report_sha256": qualification_report_hash,
        "qualification_binding_sha256": qualification_binding_hash,
        "correction_version": binding.get("correction_version"),
        "qualification_source": binding.get("qualification_scope", "qualified_subset"),
        "qualification_passed": not errors,
        "reference_supplied": False,
        "qualified_source_ids": qualified_source_ids,
        "qualified_by_source": correction_by_source,
        "binding_by_source": binding_by_source,
        "mutation_rows": sum(row.get("mutation_rows", 0) for row in binding_rows or [] if isinstance(row.get("mutation_rows", 0), int)),
        "detected_mutations": sum(row.get("detected_mutations", 0) for row in binding_rows or [] if isinstance(row.get("detected_mutations", 0), int)),
    }
    return context, sorted(set(errors))


def _make_row(
    task: dict[str, Any],
    candidate: dict[str, Any],
    attempt: dict[str, Any],
    evidence_hash: str | None,
    sidecar: dict[str, Any] | None,
    split: str,
    smoke_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_value = candidate["candidate"]
    warning_metadata = _warning_metadata(attempt)
    smoke_context = smoke_context or {}
    return {
        "schema_version": GENERATION_SFT_SCHEMA_VERSION,
        "id": f"verified_rtl_generation_{candidate['candidate_id']}",
        "dataset_name": DATASET_NAME,
        "dataset_version": "v0.1",
        "dataset_stage": DATASET_STAGE,
        "split": split,
        "source_id": task["source_id"],
        "task_id": task["task_id"],
        "candidate_id": candidate["candidate_id"],
        "attempt": candidate["attempt"],
        "candidate_sha256": candidate["candidate_sha256"],
        "source": "teacher_generated_verified",
        "license": task.get("provenance", {}).get("license"),
        "design_family": task.get("design_family"),
        "created_by": "package_verified_rtl_generation_dataset",
        "review_status": REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "promotion_allowed": False,
        "provenance": {
            "public_dataset_name": task.get("provenance", {}).get("public_dataset_name"),
            "public_dataset_url": task.get("provenance", {}).get("public_dataset_url"),
            "source_commit": task.get("provenance", {}).get("source_commit"),
            "original_source_id": task.get("provenance", {}).get("original_source_id", task["source_id"]),
            "notes": "Accepted candidate passed the configured isolated verification contract. This row is automated and unreviewed.",
        },
        "verification": {
            "profile": attempt["verification_profile"],
            "accepted": True,
            "compile_passed": True,
            "simulation_passed": True,
            "maximum_mismatch_count": 0,
            "evidence_sha256": evidence_hash,
            "runner_profile": sidecar.get("profile") if sidecar else None,
            "rtlbench_commit": sidecar.get("rtlbench_commit") if sidecar else None,
            "candidate_hash_matched": True,
            "qualification_passed": smoke_context.get("qualification_passed", False),
            "qualification_source": smoke_context.get("qualification_source"),
            "assetfix_version": smoke_context.get("correction_version"),
            "reference_supplied": smoke_context.get("reference_supplied"),
            **warning_metadata,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": deepcopy(task)},
            {"role": "assistant", "content": candidate_value["rtl"]},
        ],
    }


def validate_generation_sft_row(row: dict[str, Any], label: str = "row") -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "id", "dataset_name", "dataset_version", "dataset_stage", "split",
        "source_id", "task_id", "candidate_id", "attempt", "candidate_sha256", "source", "license",
        "design_family", "created_by", "review_status", "approval_status", "promotion_allowed",
        "provenance", "verification", "messages",
    }
    if set(row) != required:
        errors.append(f"{label} has an invalid field set")
    if row.get("schema_version") != GENERATION_SFT_SCHEMA_VERSION:
        errors.append(f"{label} has the wrong schema")
    if row.get("split") != "train":
        errors.append(f"{label} is not a training row")
    if row.get("review_status") != REVIEW_STATUS or row.get("approval_status") != APPROVAL_STATUS or row.get("promotion_allowed") is not False:
        errors.append(f"{label} has unsafe promotion metadata")
    if not isinstance(row.get("candidate_sha256"), str) or not SHA256_RE.fullmatch(row.get("candidate_sha256", "")):
        errors.append(f"{label}.candidate_sha256 is invalid")
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) != 3 or [item.get("role") for item in messages if isinstance(item, dict)] != ["system", "user", "assistant"]:
        errors.append(f"{label}.messages must be system/user/assistant")
    elif not isinstance(messages[0].get("content"), str) or not isinstance(messages[1].get("content"), dict) or not isinstance(messages[2].get("content"), str):
        errors.append(f"{label}.messages content types are invalid")
    else:
        errors.extend(_validate_task(messages[1]["content"], f"{label}.messages[1].content"))
        if _sha256_bytes(messages[2]["content"].encode("utf-8")) != row.get("candidate_sha256"):
            errors.append(f"{label}.candidate_sha256 does not match assistant RTL")
        errors.extend(_private_marker_errors(messages[2]["content"], f"{label}.messages[2].content"))
    verification = row.get("verification")
    if not isinstance(verification, dict):
        errors.append(f"{label}.verification must be an object")
    else:
        if verification.get("accepted") is not True:
            errors.append(f"{label}.verification.accepted must be true")
        if verification.get("compile_passed") is not True or verification.get("simulation_passed") is not True:
            errors.append(f"{label}.verification checks did not pass")
        if verification.get("maximum_mismatch_count") != 0:
            errors.append(f"{label}.verification has non-zero mismatches")
        if verification.get("candidate_hash_matched") is not True:
            errors.append(f"{label}.verification candidate hash was not matched")
        warning_count = verification.get("warning_count")
        if not isinstance(warning_count, int) or verification.get("warnings_present") is not (warning_count > 0):
            errors.append(f"{label}.verification warning metadata is invalid")
        if verification.get("acceptance_affected") is not False:
            errors.append(f"{label}.verification acceptance metadata is unsafe")
    return sorted(set(errors))


def validate_generation_sft_package(
    output_dir: Path,
    *,
    require_reports: bool = True,
    require_asset_qualification: bool = False,
    asset_qualification_path: Path | None = None,
    qualification_binding_path: Path | None = None,
    base_split_path: Path | None = None,
    source_acquisition_path: Path | None = None,
    asset_correction_report_path: Path | None = None,
    qualified_correction_manifest_path: Path | None = None,
    expected_source_commit: str | None = None,
    expected_source_tree_sha256: str | None = None,
    expected_frozen_split_sha256: str | None = None,
    expected_runner_profile: str | None = None,
    expected_runner_image: str | None = None,
    readiness_split_path: Path | None = None,
    require_smoke_provenance: bool = False,
    expected_package_id: str | None = None,
    expected_parent_package_id: str | None = None,
    expected_recovery_reason: str | None = None,
    require_recovery_lineage: bool = False,
    require_consumable: bool = False,
) -> tuple[dict[str, Any], int]:
    if require_recovery_lineage:
        expected_package_id = expected_package_id or RECOVERY_PACKAGE_ID
        expected_parent_package_id = expected_parent_package_id or RECOVERY_PARENT_PACKAGE_ID
        expected_recovery_reason = expected_recovery_reason or RECOVERY_REASON
    paths = _output_paths(output_dir)
    errors: list[str] = []
    counts: dict[str, int] = {}
    loaded: dict[str, list[dict[str, Any]]] = {}
    qualification_report_hash_match: bool | None = None
    pinned_task_order_match: bool | None = None
    qualified_subset_mode = False
    private_content_detected = False
    for name in ("all", "train", "rejected_rows"):
        path = paths[name]
        if not path.exists():
            errors.append(f"missing package output: {name}.jsonl")
            counts[name] = 0
            continue
        try:
            rows = _load_jsonl(path, allow_empty=True)
        except WorkflowError as exc:
            errors.append(str(exc))
            counts[name] = 0
            continue
        loaded[name] = rows
        counts[name] = len(rows)
        if name != "rejected_rows":
            for index, row in enumerate(rows, 1):
                errors.extend(validate_generation_sft_row(row, f"{name} row {index}"))
    all_ids = [row.get("id") for row in loaded.get("all", [])]
    train_ids = [row.get("id") for row in loaded.get("train", [])]
    if len(all_ids) != len(set(all_ids)):
        errors.append("all.jsonl contains duplicate row IDs")
    if len(train_ids) != len(set(train_ids)):
        errors.append("train.jsonl contains duplicate row IDs")
    if not set(train_ids).issubset(set(all_ids)):
        errors.append("train.jsonl is not contained in all.jsonl")
    for name in ("manifest", "statistics", "dataset_card"):
        if not paths[name].exists():
            errors.append(f"missing package output: {name}")
    manifest: dict[str, Any] = {}
    statistics: dict[str, Any] = {}
    if paths["manifest"].exists():
        try:
            manifest = _load_json(paths["manifest"])
            if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
                errors.append("manifest has the wrong package schema")
            if manifest.get("all_rows") != counts.get("all", 0):
                errors.append("manifest all-row count mismatch")
            if manifest.get("train_rows") != counts.get("train", 0):
                errors.append("manifest train-row count mismatch")
            if manifest.get("rejected_rows") != counts.get("rejected_rows", 0):
                errors.append("manifest rejected-row count mismatch")
            errors.extend(
                _lineage_errors(
                    manifest,
                    expected_package_id=expected_package_id,
                    expected_parent_package_id=expected_parent_package_id,
                    expected_recovery_reason=expected_recovery_reason,
                    require_recovery_lineage=require_recovery_lineage,
                    require_consumable=require_consumable,
                )
            )
            if require_asset_qualification:
                qualification_hash = manifest.get("asset_qualification_sha256")
                if manifest.get("asset_qualification_required") is not True:
                    errors.append("manifest does not require asset qualification")
                if not isinstance(qualification_hash, str) or not SHA256_RE.fullmatch(qualification_hash):
                    errors.append("manifest asset qualification hash is invalid")
            if asset_qualification_path is not None:
                try:
                    if require_smoke_provenance:
                        from scripts.dataset.rtl_generation_smoke_run import validate_qualification_report_metadata

                        _, qualification_errors = validate_qualification_report_metadata(asset_qualification_path)
                        errors.extend(qualification_errors)
                    qualification_report_hash_match = (
                        manifest.get("qualification_report_sha256") == _sha256_file(asset_qualification_path)
                        and manifest.get("asset_qualification_sha256") == _sha256_file(asset_qualification_path)
                    )
                    if not qualification_report_hash_match:
                        errors.append("manifest asset qualification hash does not match the supplied report")
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"invalid supplied asset qualification report: {exc}")
            if qualification_binding_path is not None:
                try:
                    if require_smoke_provenance:
                        from scripts.dataset.rtl_generation_qualification_binding import validate_binding_report

                        binding = validate_binding_report(qualification_binding_path)
                    else:
                        binding = _load_json(qualification_binding_path)
                        qualified_subset_mode = (
                            isinstance(binding, dict)
                            and binding.get("schema_version") == QUALIFIED_SUBSET_BINDING_SCHEMA
                        )
                        if not qualified_subset_mode:
                            from scripts.dataset.rtl_generation_qualification_binding import validate_binding_report

                            binding = validate_binding_report(qualification_binding_path)
                    if asset_qualification_path is not None:
                        if binding.get("qualification_report_sha256") != _sha256_file(asset_qualification_path):
                            qualification_report_hash_match = False
                            errors.append("manifest qualification binding does not match the supplied qualification report")
                    if manifest.get("qualification_binding_sha256") != _sha256_file(qualification_binding_path):
                        errors.append("manifest qualification binding hash does not match the supplied binding")
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"invalid supplied qualification binding: {exc}")
            if asset_qualification_path is not None and qualification_binding_path is not None and not require_smoke_provenance and not qualified_subset_mode:
                try:
                    from scripts.dataset.rtl_generation_smoke_run import validate_qualification_report_metadata
                    from scripts.dataset.rtl_generation_qualification_binding import validate_binding_report

                    _, qualification_errors = validate_qualification_report_metadata(asset_qualification_path)
                    errors.extend(qualification_errors)
                    validate_binding_report(qualification_binding_path)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"invalid supplied legacy qualification inputs: {exc}")
        except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
            errors.append(f"invalid package manifest: {exc}")
    if paths["statistics"].exists():
        try:
            statistics = _load_json(paths["statistics"])
            if statistics.get("all_rows") != counts.get("all", 0):
                errors.append("statistics all-row count mismatch")
            if statistics.get("rejected_rows") != counts.get("rejected_rows", 0):
                errors.append("statistics rejected-row count mismatch")
        except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as exc:
            errors.append(f"invalid package statistics: {exc}")
    if require_reports:
        for name in ("validation_report", "validation_report_md", "provenance_report"):
            if not paths[name].exists():
                errors.append(f"missing package output: {name}")
    if require_smoke_provenance:
        if qualification_report_hash_match is None:
            qualification_report_hash_match = False
        pinned_task_order_match = False
        expected_source_commit = expected_source_commit or SMOKE_SOURCE_COMMIT
        expected_source_tree_sha256 = expected_source_tree_sha256 or SMOKE_SOURCE_TREE_SHA256
        expected_frozen_split_sha256 = expected_frozen_split_sha256 or SMOKE_FROZEN_SPLIT_SHA256
        expected_runner_profile = expected_runner_profile or SMOKE_RUNNER_PROFILE
        expected_runner_image = expected_runner_image or SMOKE_RTLBENCH_IMAGE
        if readiness_split_path is None:
            errors.append("readiness split is required for smoke validation")
        else:
            try:
                split_by_task, split_errors = _load_split(readiness_split_path)
                errors.extend(split_errors)
                package_tasks = _package_task_records(loaded.get("all", []))
                context, context_errors = _validate_smoke_provenance(
                    tasks=package_tasks,
                    split_path=readiness_split_path,
                    split_by_task=split_by_task,
                    base_split_path=base_split_path,
                    source_acquisition_path=source_acquisition_path,
                    asset_correction_report_path=asset_correction_report_path,
                    qualified_correction_manifest_path=qualified_correction_manifest_path,
                    asset_qualification_path=asset_qualification_path,
                    qualification_binding_path=qualification_binding_path,
                    expected_source_commit=expected_source_commit,
                    expected_source_tree_sha256=expected_source_tree_sha256,
                    expected_frozen_split_sha256=expected_frozen_split_sha256,
                )
                errors.extend(context_errors)
                for field, context_key in (
                    ("source_commit", "source_commit"),
                    ("source_tree_sha256", "source_tree_sha256"),
                    ("frozen_split_sha256", "frozen_split_sha256"),
                    ("readiness_split_sha256", "readiness_split_sha256"),
                    ("source_acquisition_sha256", "source_acquisition_sha256"),
                    ("assetfix_report_sha256", "assetfix_report_sha256"),
                    ("qualified_correction_manifest_sha256", "qualified_correction_manifest_sha256"),
                    ("qualification_report_sha256", "qualification_report_sha256"),
                    ("qualification_binding_sha256", "qualification_binding_sha256"),
                ):
                    if manifest.get(field) != context.get(context_key):
                        errors.append(f"manifest {field} does not match smoke provenance")
                if manifest.get("all_rows") != 5 or manifest.get("train_rows") != 5 or manifest.get("rejected_rows") != 0:
                    errors.append("smoke manifest counts are not 5/5/0")
                if manifest.get("runner_image_id") != expected_runner_image:
                    errors.append("smoke manifest runner image mismatch")
                if manifest.get("qualification_passed") is not True or manifest.get("reference_supplied") is not False:
                    errors.append("smoke manifest qualification or reference policy mismatch")
                if statistics.get("validation_rows") != 0 or statistics.get("test_rows") != 0:
                    errors.append("smoke statistics contain validation or test rows")
                if statistics.get("accepted_rows_with_warnings") != 1 or statistics.get("warning_count") != 1 or statistics.get("acceptance_affected") is not False:
                    errors.append("smoke warning statistics are not preserved")
                package_source_ids = [row.get("source_id") for row in loaded.get("all", [])]
                package_task_ids = [row.get("task_id") for row in loaded.get("all", [])]
                pinned_task_order_match = package_source_ids == list(SMOKE_SOURCE_IDS) and package_task_ids == list(SMOKE_TASK_IDS)
                if set(package_source_ids) != set(SMOKE_SOURCE_IDS) or set(package_task_ids) != set(SMOKE_TASK_IDS):
                    errors.append("smoke package identities do not match the pinned five tasks")
                if not pinned_task_order_match:
                    errors.append("smoke package task order does not match the pinned five-row order")
                for row in loaded.get("all", []):
                    verification = row.get("verification", {})
                    if verification.get("qualification_passed") is not True or verification.get("reference_supplied") is not False:
                        errors.append(f"smoke row is not qualification-bound: {row.get('task_id')}")
                    if verification.get("runner_profile") != expected_runner_profile:
                        errors.append(f"smoke row runner profile mismatch: {row.get('task_id')}")
                    if _contains_private_marker(row):
                        private_content_detected = True
                if private_content_detected:
                    errors.append("private content detected in package rows")
            except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError, TypeError) as exc:
                errors.append(f"invalid smoke provenance inputs: {exc}")
    elif qualified_subset_mode and asset_qualification_path is not None and qualification_binding_path is not None:
        qualification_split_path = readiness_split_path or base_split_path
        if qualification_split_path is None:
            errors.append("qualified-subset validation requires a split manifest")
        elif qualified_correction_manifest_path is None:
            errors.append("qualified-subset validation requires a correction manifest")
        else:
            try:
                package_tasks = _package_task_records(loaded.get("all", []))
                context, context_errors = _validate_qualified_subset_provenance(
                    tasks=package_tasks,
                    split_path=qualification_split_path,
                    base_split_path=base_split_path,
                    source_acquisition_path=source_acquisition_path,
                    asset_qualification_path=asset_qualification_path,
                    qualification_binding_path=qualification_binding_path,
                    qualified_correction_manifest_path=qualified_correction_manifest_path,
                    expected_source_commit=expected_source_commit or "",
                    expected_source_tree_sha256=expected_source_tree_sha256 or "",
                    expected_frozen_split_sha256=expected_frozen_split_sha256 or "",
                )
                errors.extend(context_errors)
                for field, context_key in (
                    ("source_commit", "source_commit"),
                    ("source_tree_sha256", "source_tree_sha256"),
                    ("frozen_split_sha256", "frozen_split_sha256"),
                    ("readiness_split_sha256", "readiness_split_sha256"),
                    ("source_acquisition_sha256", "source_acquisition_sha256"),
                    ("qualified_correction_manifest_sha256", "qualified_correction_manifest_sha256"),
                    ("qualification_report_sha256", "qualification_report_sha256"),
                    ("qualification_binding_sha256", "qualification_binding_sha256"),
                ):
                    if manifest.get(field) != context.get(context_key):
                        errors.append(f"manifest {field} does not match qualified-subset provenance")
                if manifest.get("qualification_passed") is not True or manifest.get("reference_supplied") is not False:
                    errors.append("qualified-subset manifest qualification or reference policy mismatch")
                expected_profile = expected_runner_profile
                expected_image = expected_runner_image
                if expected_profile is not None and manifest.get("runner_profile") != expected_profile:
                    errors.append("qualified-subset manifest runner profile mismatch")
                if expected_image is not None and manifest.get("runner_image_id") != expected_image:
                    errors.append("qualified-subset manifest runner image mismatch")
                package_source_ids = [row.get("source_id") for row in loaded.get("all", [])]
                qualified_order = [source_id for source_id in context.get("qualified_source_ids", []) if source_id in package_source_ids]
                pinned_task_order_match = package_source_ids == qualified_order
                if not pinned_task_order_match:
                    errors.append("qualified-subset package task order does not match the passed qualified order")
                for row in loaded.get("all", []):
                    verification = row.get("verification", {})
                    if verification.get("qualification_passed") is not True or verification.get("reference_supplied") is not False:
                        errors.append(f"qualified-subset row is not qualification-bound: {row.get('task_id')}")
                    if expected_profile is not None and verification.get("runner_profile") != expected_profile:
                        errors.append(f"qualified-subset row runner profile mismatch: {row.get('task_id')}")
                    if _contains_private_marker(row):
                        private_content_detected = True
                if private_content_detected:
                    errors.append("private content detected in qualified-subset package rows")
            except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError, TypeError, ValueError) as exc:
                errors.append(f"invalid qualified-subset provenance inputs: {exc}")
    lineage = manifest.get("lineage") if isinstance(manifest, dict) else None
    consumable = bool(
        not errors
        and isinstance(lineage, dict)
        and lineage.get("status") == "successful_recovery_package"
        and lineage.get("consumable") is True
    ) if require_recovery_lineage else not errors
    report = {
        "ok": not errors,
        "output_dir": _display(output_dir),
        "counts": counts,
        "row_count": counts.get("all", 0),
        "train_count": counts.get("train", 0),
        "rejected_count": counts.get("rejected_rows", 0),
        "qualification_report_hash_match": qualification_report_hash_match,
        "pinned_task_order_match": pinned_task_order_match,
        "private_content_detected": private_content_detected,
        "consumable": consumable,
        "errors": sorted(set(errors)),
    }
    return report, 0 if report["ok"] else 1


def package_verified_rtl_generation_dataset(
    tasks_path: Path,
    candidates_path: Path,
    attempts_path: Path,
    split_path: Path,
    output_dir: Path,
    *,
    evidence_path: Path | None = None,
    sidecar_path: Path | None = None,
    expected_rtlbench_commit: str | None = None,
    asset_qualification_path: Path | None = None,
    qualification_binding_path: Path | None = None,
    base_split_path: Path | None = None,
    source_acquisition_path: Path | None = None,
    asset_correction_report_path: Path | None = None,
    qualified_correction_manifest_path: Path | None = None,
    expected_source_commit: str | None = None,
    expected_source_tree_sha256: str | None = None,
    expected_frozen_split_sha256: str | None = None,
    expected_runner_profile: str | None = None,
    expected_runner_image: str | None = None,
    readiness_split_path: Path | None = None,
    require_smoke_provenance: bool = False,
    require_asset_qualification: bool = False,
    max_variants: int = 3,
    force: bool = False,
    strict: bool = True,
    package_id: str | None = None,
    parent_package_id: str | None = None,
    recovery_reason: str | None = None,
    recovery_authorization_path: Path | None = None,
    require_recovery_lineage: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    if max_variants < 1:
        errors.append("max_variants must be positive")
    authorization: dict[str, Any] | None = None
    authorization_sha256: str | None = None
    if require_recovery_lineage:
        package_id = package_id or RECOVERY_PACKAGE_ID
        parent_package_id = parent_package_id or RECOVERY_PARENT_PACKAGE_ID
        recovery_reason = recovery_reason or RECOVERY_REASON
        if output_dir.exists():
            errors.append("recovery output directory must not already exist")
        authorization, authorization_errors = _load_recovery_authorization(
            recovery_authorization_path,
            expected_package_id=package_id,
            expected_parent_package_id=parent_package_id,
            expected_recovery_reason=recovery_reason,
        )
        errors.extend(authorization_errors)
        if recovery_authorization_path is not None and recovery_authorization_path.is_file():
            authorization_sha256 = _sha256_file(recovery_authorization_path)
    if require_smoke_provenance:
        expected_source_commit = expected_source_commit or SMOKE_SOURCE_COMMIT
        expected_source_tree_sha256 = expected_source_tree_sha256 or SMOKE_SOURCE_TREE_SHA256
        expected_frozen_split_sha256 = expected_frozen_split_sha256 or SMOKE_FROZEN_SPLIT_SHA256
        expected_runner_profile = expected_runner_profile or SMOKE_RUNNER_PROFILE
        expected_runner_image = expected_runner_image or SMOKE_RTLBENCH_IMAGE
        if expected_source_commit != SMOKE_SOURCE_COMMIT:
            errors.append("unexpected smoke source commit")
        if expected_source_tree_sha256 != SMOKE_SOURCE_TREE_SHA256:
            errors.append("unexpected smoke source-tree hash")
        if expected_frozen_split_sha256 != SMOKE_FROZEN_SPLIT_SHA256:
            errors.append("unexpected smoke frozen-split hash")
        if expected_runner_profile != SMOKE_RUNNER_PROFILE:
            errors.append("unexpected smoke runner profile")
        if expected_runner_image != SMOKE_RTLBENCH_IMAGE:
            errors.append("unexpected smoke runner image")
    errors.extend(_check_output_dir(output_dir, force))
    qualification_report: dict[str, Any] | None = None
    qualified_subset_mode = False
    if require_asset_qualification and asset_qualification_path is None:
        errors.append("asset qualification report is required for this package")
    if asset_qualification_path is not None:
        try:
            qualification_report = _load_json(asset_qualification_path)
            if require_smoke_provenance:
                from scripts.dataset.rtl_generation_smoke_run import validate_qualification_report_metadata

                qualification_report, qualification_errors = validate_qualification_report_metadata(asset_qualification_path)
                errors.extend(qualification_errors)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid asset qualification report: {exc}")
    qualification_binding: dict[str, Any] | None = None
    if qualification_binding_path is not None:
        try:
            qualification_binding = _load_json(qualification_binding_path)
            qualified_subset_mode = (
                isinstance(qualification_binding, dict)
                and qualification_binding.get("schema_version") == QUALIFIED_SUBSET_BINDING_SCHEMA
            )
            if require_smoke_provenance or not qualified_subset_mode:
                from scripts.dataset.rtl_generation_qualification_binding import validate_binding_report

                qualification_binding = validate_binding_report(qualification_binding_path)
            if asset_qualification_path is None:
                errors.append("qualification binding requires an asset qualification report")
            elif isinstance(qualification_binding, dict) and qualification_binding.get("qualification_report_sha256") != _sha256_file(asset_qualification_path):
                errors.append("qualification binding does not match the supplied asset qualification report")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid qualification binding: {exc}")
    if not require_smoke_provenance and not qualified_subset_mode and asset_qualification_path is not None and qualification_binding_path is not None:
        try:
            from scripts.dataset.rtl_generation_smoke_run import validate_qualification_report_metadata
            from scripts.dataset.rtl_generation_qualification_binding import validate_binding_report

            qualification_report, qualification_errors = validate_qualification_report_metadata(asset_qualification_path)
            errors.extend(qualification_errors)
            qualification_binding = validate_binding_report(qualification_binding_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid legacy qualification inputs: {exc}")
    try:
        tasks = _load_jsonl(tasks_path)
        candidates = _load_jsonl(candidates_path)
        attempts = _load_jsonl(attempts_path)
        split_by_task, split_errors = _load_split(split_path)
        errors.extend(split_errors)
    except WorkflowError as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    smoke_context: dict[str, Any] = {}
    if require_smoke_provenance:
        smoke_context, smoke_errors = _validate_smoke_provenance(
            tasks=tasks,
            split_path=split_path,
            split_by_task=split_by_task,
            base_split_path=base_split_path,
            source_acquisition_path=source_acquisition_path,
            asset_correction_report_path=asset_correction_report_path,
            qualified_correction_manifest_path=qualified_correction_manifest_path,
            asset_qualification_path=asset_qualification_path,
            qualification_binding_path=qualification_binding_path,
            expected_source_commit=expected_source_commit or SMOKE_SOURCE_COMMIT,
            expected_source_tree_sha256=expected_source_tree_sha256 or SMOKE_SOURCE_TREE_SHA256,
            expected_frozen_split_sha256=expected_frozen_split_sha256 or SMOKE_FROZEN_SPLIT_SHA256,
        )
        errors.extend(smoke_errors)
        if smoke_context:
            smoke_context["qualification_passed"] = True
    elif qualified_subset_mode and asset_qualification_path is not None and qualification_binding_path is not None:
        qualified_context, qualified_errors = _validate_qualified_subset_provenance(
            tasks=tasks,
            split_path=split_path,
            base_split_path=base_split_path,
            source_acquisition_path=source_acquisition_path,
            asset_qualification_path=asset_qualification_path,
            qualification_binding_path=qualification_binding_path,
            qualified_correction_manifest_path=qualified_correction_manifest_path,
            expected_source_commit=expected_source_commit or "",
            expected_source_tree_sha256=expected_source_tree_sha256 or "",
            expected_frozen_split_sha256=expected_frozen_split_sha256 or "",
        )
        errors.extend(qualified_errors)
        if qualified_context:
            smoke_context = qualified_context
            qualification_report = _load_json(asset_qualification_path)
            qualification_binding = _load_json(qualification_binding_path)
    elif require_asset_qualification:
        errors.append("qualified-subset qualification binding is required for this package")
    evidence_rows, evidence_hash_by_candidate, sidecar_by_candidate, evidence_errors = _load_evidence_bundle(
        evidence_path,
        sidecar_path,
        expected_commit=expected_rtlbench_commit,
        expected_profile=expected_runner_profile,
        expected_image=expected_runner_image,
        strict=strict,
    )
    errors.extend(evidence_errors)
    if require_smoke_provenance:
        if len(tasks) != 5:
            errors.append("smoke package requires exactly five task rows")
        if len(candidates) != 5:
            errors.append("smoke package requires exactly five candidate rows")
        if len(attempts) != 5:
            errors.append("smoke package requires exactly five attempt rows")
        if len(evidence_rows) != 5:
            errors.append("smoke package requires exactly five evidence rows")

    task_by_id: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(tasks, 1):
        errors.extend(_validate_task(task, f"task row {index}"))
        task_id = task.get("task_id")
        if isinstance(task_id, str) and task_id in task_by_id:
            errors.append(f"duplicate task_id: {task_id}")
        elif isinstance(task_id, str):
            task_by_id[task_id] = task

    candidate_by_id: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates, 1):
        errors.extend(_validate_candidate_record(candidate, f"candidate row {index}"))
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id in candidate_by_id:
            errors.append(f"duplicate candidate_id: {candidate_id}")
        elif isinstance(candidate_id, str):
            candidate_by_id[candidate_id] = candidate

    attempt_by_candidate: dict[str, dict[str, Any]] = {}
    for index, attempt in enumerate(attempts, 1):
        errors.extend(_validate_package_attempt_row(attempt, f"attempt row {index}"))
        candidate_id = attempt.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id in attempt_by_candidate:
            errors.append(f"duplicate attempt candidate_id: {candidate_id}")
        elif isinstance(candidate_id, str):
            attempt_by_candidate[candidate_id] = attempt

    evidence_by_candidate: dict[str, dict[str, Any]] = {}
    for index, evidence in enumerate(evidence_rows, 1):
        candidate_id = evidence.get("candidate_id")
        matching_attempt = attempt_by_candidate.get(candidate_id) if isinstance(candidate_id, str) else None
        if matching_attempt is None:
            errors.append(f"evidence row {index} has no matching attempt")
        else:
            errors.extend(_validate_evidence_row(evidence, matching_attempt, f"evidence row {index}"))
        if isinstance(candidate_id, str):
            if candidate_id in evidence_by_candidate:
                errors.append(f"duplicate evidence candidate_id: {candidate_id}")
            evidence_by_candidate[candidate_id] = evidence

    accepted_by_task: dict[ str, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, dict[str, Any] | None]]] = {}
    rejected: list[dict[str, Any]] = []
    qualified_source_ids = set(smoke_context.get("qualified_source_ids", []))
    failed_attempts_by_task: dict[str, list[dict[str, Any]]] = {}
    for candidate_id, attempt in sorted(attempt_by_candidate.items()):
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            errors.append(f"attempt has no candidate record: {candidate_id}")
            continue
        task_id = attempt.get("task_id")
        task = task_by_id.get(task_id)
        if task is None:
            errors.append(f"candidate attempt references unknown task: {task_id}")
            continue
        if candidate.get("task_id") != task_id or candidate.get("source_id") != task.get("source_id") or candidate.get("attempt") != attempt.get("attempt"):
            errors.append(f"candidate/attempt/task identity mismatch: {candidate_id}")
        if candidate.get("candidate_sha256") != attempt.get("candidate_sha256"):
            errors.append(f"candidate hash mismatch in attempt history: {candidate_id}")
        split_row = split_by_task.get(task_id)
        if split_row is None:
            errors.append(f"task is absent from frozen split: {task_id}")
            continue
        split_name = split_row.get("split")
        if split_name != "train":
            if attempt.get("accepted"):
                rejected.append({"task_id": task_id, "source_id": task.get("source_id"), "reason": "accepted non-training candidate excluded"})
            continue
        if split_row.get("verification_readiness") != "executable_ready" and task.get("source_id") not in qualified_source_ids:
            rejected.append({"task_id": task_id, "source_id": task.get("source_id"), "reason": "task is not executable_ready"})
            continue
        if asset_qualification_path is not None:
            qualified_ids = {
                row.get("source_id")
                for row in (qualification_report or {}).get("rows", [])
                if isinstance(row, dict) and row.get("qualification_passed") is True
            }
            if task.get("source_id") not in qualified_ids:
                errors.append(f"task has no passed asset qualification: {task_id}")
                continue
        if qualification_binding is not None:
            binding_rows = {
                row.get("source_id"): row
                for row in qualification_binding.get("rows", [])
                if isinstance(row, dict)
            }
            binding_row = binding_rows.get(task.get("source_id"))
            if binding_row is None or binding_row.get("qualification_result") != "passed":
                errors.append(f"task has no passed qualification binding: {task_id}")
                continue
        evidence = evidence_by_candidate.get(candidate_id)
        evidence_hash = evidence_hash_by_candidate.get(candidate_id)
        runner_identity = sidecar_by_candidate.get(candidate_id)
        if evidence is not None:
            input_hashes = evidence.get("input_hashes") if isinstance(evidence.get("input_hashes"), dict) else {}
            if input_hashes.get("candidate_rtl_sha256") != candidate.get("candidate_sha256"):
                errors.append(f"evidence candidate hash mismatch: {candidate_id}")
            if qualification_binding is not None and input_hashes.get("testbench_sha256") != binding_row.get("corrected_testbench_sha256"):
                errors.append(f"evidence testbench hash is not the qualified corrected asset: {candidate_id}")
        elif strict and attempt.get("accepted") is True:
            errors.append(f"accepted attempt has no evidence: {candidate_id}")
        if strict and attempt.get("accepted") is True and runner_identity is None:
            errors.append(f"accepted attempt has no runner sidecar: {candidate_id}")
        if attempt.get("accepted") is True:
            accepted_by_task.setdefault(task_id, []).append((candidate, attempt, task, evidence_hash, runner_identity))
        else:
            failed_attempts_by_task.setdefault(task_id, []).append(attempt)

    primary_rows: list[dict[str, Any]] = []
    augmentation_rows: list[dict[str, Any]] = []
    task_order = [task.get("task_id") for task in tasks if isinstance(task.get("task_id"), str)]
    ordered_task_ids = task_order if (require_smoke_provenance or require_recovery_lineage or qualification_binding is not None) else sorted(accepted_by_task)
    for task_id in ordered_task_ids:
        if task_id not in accepted_by_task:
            continue
        accepted = sorted(accepted_by_task[task_id], key=lambda item: (item[0].get("attempt", 0), item[0].get("candidate_id", "")))
        seen_normalized: set[str] = set()
        chosen = 0
        for candidate, attempt, task, evidence_hash, runner_identity in accepted:
            normalized_hash = _normalized_rtl_hash(candidate["candidate"]["rtl"])
            exact_hash = candidate["candidate_sha256"]
            if exact_hash in seen_normalized or normalized_hash in seen_normalized:
                rejected.append({"task_id": task_id, "source_id": task["source_id"], "candidate_id": candidate["candidate_id"], "reason": "duplicate accepted RTL"})
                continue
            seen_normalized.add(exact_hash)
            seen_normalized.add(normalized_hash)
            row = _make_row(task, candidate, attempt, evidence_hash, runner_identity, "train", smoke_context)
            if chosen == 0:
                primary_rows.append(row)
            elif chosen < max_variants:
                augmentation_rows.append(row)
            else:
                rejected.append({"task_id": task_id, "source_id": task["source_id"], "candidate_id": candidate["candidate_id"], "reason": "accepted variant limit exceeded"})
            chosen += 1

    accepted_task_ids = set(accepted_by_task)
    for task_id, task in sorted(task_by_id.items()):
        split_row = split_by_task.get(task_id)
        if split_row and split_row.get("split") == "train" and (split_row.get("verification_readiness") == "executable_ready" or task.get("source_id") in qualified_source_ids) and task_id not in accepted_task_ids:
            failed_attempts = failed_attempts_by_task.get(task_id, [])
            failure_category = failed_attempts[-1].get("failure_category") if failed_attempts else None
            reason = (
                f"{failure_category}_pending_repair"
                if failure_category in {"compile_failure", "functional_mismatch", "timeout"}
                else "no accepted candidate"
            )
            rejected.append({"task_id": task_id, "source_id": task.get("source_id"), "reason": reason})

    if errors and strict:
        return {"ok": False, "errors": sorted(set(errors)), "accepted_rows": 0, "rejected_rows": len(rejected)}, 1

    paths = _output_paths(output_dir)
    all_rows = primary_rows + augmentation_rows
    all_bytes = b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in all_rows)
    train_bytes = b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in primary_rows)
    rejected_bytes = b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in sorted(rejected, key=lambda row: (str(row.get("task_id")), str(row.get("candidate_id", "")), str(row.get("reason")))))
    warning_rows = [
        row for row in all_rows
        if row.get("verification", {}).get("warnings_present") is True
    ]
    warning_count = sum(
        row.get("verification", {}).get("warning_count", 0)
        for row in warning_rows
    )
    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "dataset_name": DATASET_NAME,
        "dataset_version": "v0.1",
        "created_by": "package_verified_rtl_generation_dataset",
        "tasks_sha256": _sha256_file(tasks_path),
        "candidates_sha256": _sha256_file(candidates_path),
        "attempts_sha256": _sha256_file(attempts_path),
        "split_manifest_sha256": _sha256_file(split_path),
        "frozen_split_sha256": smoke_context.get("frozen_split_sha256"),
        "readiness_split_sha256": smoke_context.get("readiness_split_sha256"),
        "source_commit": smoke_context.get("source_commit"),
        "source_tree_sha256": smoke_context.get("source_tree_sha256"),
        "source_acquisition_sha256": smoke_context.get("source_acquisition_sha256"),
        "assetfix_report_sha256": smoke_context.get("assetfix_report_sha256"),
        "qualified_correction_manifest_sha256": smoke_context.get("qualified_correction_manifest_sha256"),
        "asset_qualification_required": require_asset_qualification or asset_qualification_path is not None,
        "asset_qualification_sha256": _sha256_file(asset_qualification_path) if asset_qualification_path is not None and asset_qualification_path.is_file() else None,
        "qualification_report_sha256": _sha256_file(asset_qualification_path) if asset_qualification_path is not None and asset_qualification_path.is_file() else None,
        "qualification_binding_required": qualification_binding_path is not None,
        "qualification_binding_sha256": _sha256_file(qualification_binding_path) if qualification_binding_path is not None and qualification_binding_path.is_file() else None,
        "evidence_sha256": _sha256_file(evidence_path) if evidence_path is not None and evidence_path.is_file() else None,
        "evidence_file_count": len(_evidence_files(evidence_path)) if evidence_path is not None else 0,
        "qualification_passed": smoke_context.get("qualification_passed", False),
        "qualification_source": smoke_context.get("qualification_source"),
        "mutation_rows": smoke_context.get("mutation_rows"),
        "detected_mutations": smoke_context.get("detected_mutations"),
        "reference_supplied": smoke_context.get("reference_supplied"),
        "runner_profile": expected_runner_profile,
        "runner_image_id": expected_runner_image,
        "all_rows": len(all_rows),
        "train_rows": len(primary_rows),
        "augmentation_rows": len(augmentation_rows),
        "rejected_rows": len(rejected),
        "validation_rows": 0,
        "test_rows": 0,
        "warnings_present": bool(warning_rows),
        "warning_count": warning_count,
        "acceptance_affected": False,
        "review_status": REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "promotion_allowed": False,
        "errors": sorted(set(errors)),
    }
    if require_recovery_lineage:
        manifest["package_id"] = package_id
        manifest["lineage"] = {
            "schema_version": PACKAGE_LINEAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "parent_package_id": parent_package_id,
            "recovery_reason": recovery_reason,
            "authorization_sha256": authorization_sha256,
            "status": "pending_validation",
            "consumable": False,
        }
    statistics = {
        "schema_version": "rtl_verified_generation_statistics_v0.1",
        "candidate_records": len(candidates),
        "attempt_records": len(attempts),
        "all_rows": len(all_rows),
        "accepted_tasks": len(primary_rows),
        "augmentation_rows": len(augmentation_rows),
        "rejected_rows": len(rejected),
        "accepted_attempts": sum(1 for attempt in attempts if attempt.get("accepted") is True),
        "validation_rows": 0,
        "test_rows": 0,
        "accepted_rows_with_warnings": len(warning_rows),
        "warning_count": warning_count,
        "warnings_present": bool(warning_rows),
        "acceptance_affected": False,
        "normalization_accepted": 5 if require_smoke_provenance else None,
        "assets_qualified": 5 if require_smoke_provenance else None,
        "mutation_rows": smoke_context.get("mutation_rows"),
        "detected_mutations": smoke_context.get("detected_mutations"),
        "repairs": 0 if require_smoke_provenance else None,
        "compile_pass_rate": 1.0 if require_smoke_provenance else None,
        "simulation_pass_rate": 1.0 if require_smoke_provenance else None,
        "reference_exposures": 0,
        "infrastructure_failures": 0,
        "attempts_per_accepted_task": {
            row["source_id"]: row["attempt"] for row in primary_rows
        },
    }
    dataset_card = (
        "# Verified RTL generation dataset v0.1\n\n"
        "This package contains automated, isolated-verification-backed RTL generation rows. "
        "It is not human-approved or promotion-authorized.\n\n"
        f"- All accepted training rows: {len(all_rows)}\n"
        f"- Primary training rows: {len(primary_rows)}\n"
        f"- Augmentation rows: {len(augmentation_rows)}\n"
        f"- Rejected/excluded rows: {len(rejected)}\n"
        f"- Accepted rows with sanitized warnings: {len(warning_rows)}\n"
        "- Private reference RTL, testbenches, support files, and raw logs: excluded\n"
        "- Review status: automated_verified_unreviewed\n"
        "- Promotion allowed: false\n"
    )
    try:
        _write_atomic(paths["all"], all_bytes)
        _write_atomic(paths["train"], train_bytes)
        _write_atomic(paths["rejected_rows"], rejected_bytes)
        _write_atomic(paths["manifest"], (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write_atomic(paths["statistics"], (json.dumps(statistics, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write_atomic(paths["dataset_card"], dataset_card.encode("utf-8"))
    except (OSError, ValueError, WorkflowError) as exc:
        return {"ok": False, "errors": [str(exc)], "accepted_rows": 0}, 1
    validation_report, validation_code = validate_generation_sft_package(
        output_dir,
        require_reports=False,
        require_asset_qualification=require_asset_qualification or asset_qualification_path is not None,
        asset_qualification_path=asset_qualification_path,
        qualification_binding_path=qualification_binding_path,
        base_split_path=base_split_path,
        source_acquisition_path=source_acquisition_path,
        asset_correction_report_path=asset_correction_report_path,
        qualified_correction_manifest_path=qualified_correction_manifest_path,
        expected_source_commit=expected_source_commit,
        expected_source_tree_sha256=expected_source_tree_sha256,
        expected_frozen_split_sha256=expected_frozen_split_sha256,
        expected_runner_profile=expected_runner_profile,
        expected_runner_image=expected_runner_image,
        readiness_split_path=readiness_split_path or split_path,
        require_smoke_provenance=require_smoke_provenance,
        expected_package_id=package_id,
        expected_parent_package_id=parent_package_id,
        expected_recovery_reason=recovery_reason,
        require_recovery_lineage=require_recovery_lineage,
        require_consumable=False,
    )
    validation_markdown = (
        "# Verified RTL generation smoke package v001\n\n"
        f"- Validation result: {'passed' if validation_code == 0 else 'failed'}\n"
        f"- All rows: {len(all_rows)}\n"
        f"- Training rows: {len(primary_rows)}\n"
        f"- Rejected rows: {len(rejected)}\n"
        "- Validation rows: 0\n"
        "- Test rows: 0\n"
        f"- Accepted rows with sanitized warnings: {len(warning_rows)}\n"
        f"- Warning count: {warning_count}\n"
        "- Warning affected acceptance: false\n"
        "- Qualification: passed\n"
        "- Reference RTL supplied: false\n"
        "- Promotion allowed: false\n\n"
        "Private RTL, testbenches, mutation content, paths, and raw simulator output are excluded.\n"
    )
    if require_recovery_lineage:
        manifest["lineage"]["status"] = (
            "pending_finalization"
            if not errors and validation_code == 0
            else "failed_partial_package"
        )
        manifest["lineage"]["consumable"] = False
        validation_markdown += (
            f"- Package ID: {manifest['lineage']['package_id']}\n"
            f"- Parent package: {manifest['lineage']['parent_package_id']}\n"
            f"- Recovery reason: {manifest['lineage']['recovery_reason']}\n"
            f"- Consumable: {'true' if manifest['lineage']['consumable'] else 'false'}\n"
        )
        validation_report["lineage"] = manifest["lineage"]
        validation_report["consumable"] = manifest["lineage"]["consumable"]
    _write_atomic(
        paths["manifest"],
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    provenance_report = {
        "schema_version": "rtl_verified_generation_provenance_v0.1",
        "tasks_sha256": manifest["tasks_sha256"],
        "candidates_sha256": manifest["candidates_sha256"],
        "attempts_sha256": manifest["attempts_sha256"],
        "split_manifest_sha256": manifest["split_manifest_sha256"],
        "evidence_sha256": manifest["evidence_sha256"],
        "evidence_file_count": manifest["evidence_file_count"],
        "asset_qualification_required": manifest["asset_qualification_required"],
        "asset_qualification_sha256": manifest["asset_qualification_sha256"],
        "qualification_report_sha256": manifest["qualification_report_sha256"],
        "qualification_binding_required": manifest["qualification_binding_required"],
        "qualification_binding_sha256": manifest["qualification_binding_sha256"],
        "frozen_split_sha256": manifest["frozen_split_sha256"],
        "readiness_split_sha256": manifest["readiness_split_sha256"],
        "source_commit": manifest["source_commit"],
        "source_tree_sha256": manifest["source_tree_sha256"],
        "source_acquisition_sha256": manifest["source_acquisition_sha256"],
        "assetfix_report_sha256": manifest["assetfix_report_sha256"],
        "qualified_correction_manifest_sha256": manifest["qualified_correction_manifest_sha256"],
        "runner_profile": manifest["runner_profile"],
        "runner_image_id": manifest["runner_image_id"],
        "qualification_passed": manifest["qualification_passed"],
        "reference_supplied": manifest["reference_supplied"],
        "mutation_rows": manifest["mutation_rows"],
        "detected_mutations": manifest["detected_mutations"],
        "warnings_present": manifest["warnings_present"],
        "warning_count": manifest["warning_count"],
        "acceptance_affected": manifest["acceptance_affected"],
        "review_status": REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "promotion_allowed": False,
    }
    if require_recovery_lineage:
        provenance_report["package_id"] = manifest["lineage"]["package_id"]
        provenance_report["lineage"] = manifest["lineage"]
    try:
        _write_atomic(paths["validation_report"], (json.dumps(validation_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        _write_atomic(paths["validation_report_md"], validation_markdown.encode("utf-8"))
        _write_atomic(paths["provenance_report"], (json.dumps(provenance_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    except (OSError, ValueError, WorkflowError) as exc:
        return {"ok": False, "errors": [str(exc)], "accepted_rows": 0}, 1
    final_validation_report, final_validation_code = validate_generation_sft_package(
        output_dir,
        require_asset_qualification=require_asset_qualification or asset_qualification_path is not None,
        asset_qualification_path=asset_qualification_path,
        qualification_binding_path=qualification_binding_path,
        base_split_path=base_split_path,
        source_acquisition_path=source_acquisition_path,
        asset_correction_report_path=asset_correction_report_path,
        qualified_correction_manifest_path=qualified_correction_manifest_path,
        expected_source_commit=expected_source_commit,
        expected_source_tree_sha256=expected_source_tree_sha256,
        expected_frozen_split_sha256=expected_frozen_split_sha256,
        expected_runner_profile=expected_runner_profile,
        expected_runner_image=expected_runner_image,
        readiness_split_path=readiness_split_path or split_path,
        require_smoke_provenance=require_smoke_provenance,
        expected_package_id=package_id,
        expected_parent_package_id=parent_package_id,
        expected_recovery_reason=recovery_reason,
        require_recovery_lineage=require_recovery_lineage,
        require_consumable=False,
    )
    result = {
        "ok": not errors and final_validation_code == 0,
        "output_dir": _display(output_dir),
        "all_rows": len(all_rows),
        "primary_rows": len(primary_rows),
        "augmentation_rows": len(augmentation_rows),
        "rejected_rows": len(rejected),
        "validation": final_validation_report,
        "errors": sorted(set(errors)) + final_validation_report.get("errors", []),
        "warnings": [],
    }
    return result, 0 if result["ok"] else 1


__all__ = [
    "DATASET_NAME",
    "GENERATION_SFT_SCHEMA_VERSION",
    "PACKAGE_SCHEMA_VERSION",
    "package_verified_rtl_generation_dataset",
    "validate_generation_sft_package",
    "validate_generation_sft_row",
]
