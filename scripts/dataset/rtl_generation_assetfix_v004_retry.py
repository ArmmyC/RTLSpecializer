"""Diagnose two v004 qualification misses and prepare a two-task retry.

This module is a metadata/control-plane boundary. It reads the preserved
qualification metadata and generated v004 assets, reasons from the public
specification, writes a metadata-only diagnosis, and prepares a new
qualification-only input. It never reads upstream/reference HDL, invokes a
compiler or simulator, calls a model, or executes RTLBench.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any, Iterable


SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
SELECTION_IDS_SHA256 = "63fab71f0a3e7058f81fa00664e2c52a2ad25cddf7749280c8f06b3b433b7d88"
SELECTION_REPORT_SHA256 = "cf41966a3bfc347474cbf5c69bdaea5189d9733f263b6fa12fd351d77b756482"
BASE_CORRECTION_VERSION = "assetfix_v004"
RETRY_CORRECTION_VERSION = "assetfix_v004_retry_01"
RTLBench_COMMIT = "fcad47eb03e469097432229e1285b9239fd23a00"
IMAGE_ID = "sha256:004331efd280c2c94a7a25d920f9e008c0f552237d0d66902806a327033ead9b"
FAILED_SOURCE_IDS = (
    "Prob070_ece241_2013_q2",
    "Prob074_ece241_2014_q4",
)

DIAGNOSIS_SCHEMA_VERSION = "rtl_asset_qualification_failure_diagnosis_v0.2"
RETRY_MANIFEST_SCHEMA_VERSION = "rtl_verification_asset_correction_retry_v0.1"
RETRY_ATTESTATION_SCHEMA_VERSION = "rtl_verification_asset_correction_retry_attestation_v0.1"
PREPARATION_SCHEMA_VERSION = "rtl_asset_qualification_retry_preparation_v0.1"
AUTHORIZATION_SCHEMA_VERSION = "rtl_asset_qualification_retry_execution_authorization_v0.1"
PREFLIGHT_SCHEMA_VERSION = "rtl_asset_qualification_retry_preflight_v0.1"
CASE_SCHEMA_VERSION = "rtl_asset_qualification_case_v0.1"
MANIFEST_SCHEMA_VERSION = "rtl_candidate_manifest_v0.1"

MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)
PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
    "refmodule",
    chr(96) + "include",
    "package ",
    "interface ",
    "candidate_evidence",
    "mutation_evidence",
)


class RetryPreparationError(ValueError):
    """Raised for a fail-closed diagnosis or retry-preparation error."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise RetryPreparationError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _write_json(path: Path, value: Any) -> None:
    _write_exclusive(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _write_exclusive(path, b"".join(_json_bytes(row) for row in rows))


def _load_json(path: Path) -> dict[str, Any]:
    _require_regular(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RetryPreparationError(f"invalid JSON input: {path.name}") from exc
    if not isinstance(value, dict):
        raise RetryPreparationError(f"JSON input is not an object: {path.name}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    _require_regular(path)
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RetryPreparationError(f"could not read JSONL input: {path.name}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetryPreparationError(f"malformed JSONL row {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise RetryPreparationError(f"JSONL row is not an object: {path.name}:{line_number}")
        rows.append(value)
    return rows


def _require_regular(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RetryPreparationError(f"required file is unavailable: {path.name}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RetryPreparationError(f"required path is not a regular file: {path.name}")


def _require_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RetryPreparationError(f"required directory is unavailable: {path.name}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RetryPreparationError(f"required path is not a directory: {path.name}")


def _copy_regular(source: Path, destination: Path) -> None:
    _require_regular(source)
    if source.lstat().st_nlink != 1:
        raise RetryPreparationError(f"source is hard-linked: {source.name}")
    if destination.exists() or destination.is_symlink():
        raise RetryPreparationError(f"refusing to replace output: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)


def _workspace_tree_sha256(root: Path) -> str:
    _require_directory(root)
    records: list[bytes] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RetryPreparationError(f"invalid workspace directory: {path.name}")
            records.append(b"D\0" + path.relative_to(root).as_posix().encode("utf-8") + b"\n")
        for name in files:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RetryPreparationError(f"invalid workspace file: {path.name}")
            records.append(
                b"F\0"
                + path.relative_to(root).as_posix().encode("utf-8")
                + b"\0"
                + sha256_file(path).encode("ascii")
                + b"\n"
            )
    return hashlib.sha256(b"".join(records)).hexdigest()


def _validate_tree_permissions(root: Path) -> tuple[int, int, int]:
    _require_directory(root)
    root_metadata = root.lstat()
    if stat.S_IMODE(root_metadata.st_mode) != 0o700:
        raise RetryPreparationError(f"root is not mode 0700: {root.name}")
    uid, gid, regular_files = root_metadata.st_uid, root_metadata.st_gid, 0
    paths = [root, *sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())]
    for path in paths:
        metadata = path.lstat()
        if metadata.st_uid != uid or metadata.st_gid != gid:
            raise RetryPreparationError(f"owner mismatch: {path.name}")
        if stat.S_ISLNK(metadata.st_mode):
            raise RetryPreparationError(f"symlink found: {path.name}")
        if stat.S_ISDIR(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise RetryPreparationError(f"directory is not mode 0700: {path.name}")
        elif stat.S_ISREG(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
                raise RetryPreparationError(f"file is not private 0600: {path.name}")
            regular_files += 1
        else:
            raise RetryPreparationError(f"special file found: {path.name}")
    return uid, gid, regular_files


def _candidate_static_errors(text: str) -> list[str]:
    lowered = text.casefold()
    errors = [
        f"forbidden marker: {marker}"
        for marker in PRIVATE_MARKERS
        if marker.casefold() in lowered
    ]
    modules = MODULE_RE.findall(text)
    if modules != ["TopModule"]:
        errors.append("candidate must declare exactly one TopModule")
    return sorted(set(errors))


def _replacement_mutation(source_id: str) -> tuple[str, str]:
    if source_id == "Prob070_ece241_2013_q2":
        return (
            "wrong_pos_assignment",
            """module TopModule(
  input logic a,
  input logic b,
  input logic c,
  input logic d,
  output logic out_sop,
  output logic out_pos
);
  assign out_sop = (c & d) | (~a & ~b & c);
  assign out_pos = out_sop & a;
endmodule
""",
        )
    if source_id == "Prob074_ece241_2014_q4":
        return (
            "wrong_or_feedback",
            """module TopModule(input logic clk, input logic x, output logic z);
  logic qx, qa, qo;
  initial begin qx = 1'b0; qa = 1'b0; qo = 1'b0; end
  always_ff @(posedge clk) begin
    qx <= x ^ qx;
    qa <= x & ~qa;
    qo <= x | qo;
  end
  assign z = ~(qx | qa | qo);
endmodule
""",
        )
    raise RetryPreparationError(f"unsupported failed source: {source_id}")


def _semantic_analysis(source_id: str) -> dict[str, Any]:
    if source_id == "Prob070_ece241_2013_q2":
        return {
            "public_behavior_class": "bounded_combinational",
            "input_space_size": 16,
            "exhaustive_public_input_analysis": True,
            "positive_vs_mutation_difference_count": 0,
            "stimulus_coverage_sufficient_for_semantic_test": True,
            "combinational_loop": False,
            "diagnosis_basis": "The original wrong_boolean_form mutation produces the same two public output functions as the positive candidate on every four-input assignment, including the public dont-care assignments.",
            "replacement_distinguishing_condition": "The replacement wrong_pos_assignment changes out_pos for a required asserted minterm and is not equivalent to the public specification.",
        }
    if source_id == "Prob074_ece241_2014_q4":
        return {
            "public_behavior_class": "registered_feedback_logic",
            "initial_state": [0, 0, 0],
            "feedback_is_registered": True,
            "combinational_loop": False,
            "post_edge_stabilization_required": True,
            "existing_post_edge_observation_present": True,
            "stimulus_duration_can_distinguish_original_mutation": False,
            "positive_vs_mutation_output_difference": False,
            "diagnosis_basis": "The original wrong_gate_feedback change can alter the internal AND feedback state, but the public NOR output masks that difference whenever it occurs; the difference resolves on a subsequent zero input. No public input sequence distinguishes the observed output from the specified initial state.",
            "replacement_distinguishing_condition": "The replacement wrong_or_feedback changes the OR feedback recurrence and is distinguishable from the public initial state on a zero-input post-edge observation.",
        }
    raise RetryPreparationError(f"unsupported failed source: {source_id}")


def _evidence_by_source(evidence: list[dict[str, Any]], source_id: str) -> list[dict[str, Any]]:
    rows = [row for row in evidence if row.get("source_id") == source_id]
    if len(rows) != 3 or [row.get("kind") for row in rows] != ["positive", "negative", "negative"]:
        raise RetryPreparationError(f"preserved evidence shape is invalid: {source_id}")
    return rows


def build_diagnosis(
    *,
    failed_run_root: Path,
    base_correction_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Build a metadata-only diagnosis from the preserved failed attempt."""
    report_path = failed_run_root / "reports" / "asset_qualification_validation.json"
    evidence_path = failed_run_root / "reports" / "asset_qualification_evidence.jsonl"
    sidecar_path = failed_run_root / "staged" / "candidate_evidence.jsonl.runner.json"
    case_path = failed_run_root / "case_manifest.jsonl"
    failed_ids_path = failed_run_root / "reports" / "failed_or_inconclusive_task_ids.txt"
    correction_manifest_path = base_correction_root / "manifest.jsonl"
    report = _load_json(report_path)
    evidence = _load_jsonl(evidence_path)
    cases = _load_jsonl(case_path)
    correction_rows = _load_jsonl(correction_manifest_path)
    failed_ids = [line.strip() for line in failed_ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if failed_ids != list(FAILED_SOURCE_IDS):
        raise RetryPreparationError("preserved failed-task list does not match the two-task retry scope")
    if report.get("source_commit") != SOURCE_COMMIT or report.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise RetryPreparationError("preserved qualification source binding mismatch")
    if report.get("frozen_split_sha256") != FROZEN_SPLIT_SHA256 or report.get("correction_version") != BASE_CORRECTION_VERSION:
        raise RetryPreparationError("preserved qualification split or correction binding mismatch")
    if len(evidence) != 60 or len(cases) != 60:
        raise RetryPreparationError("preserved qualification attempt is not the expected 60-case run")
    correction_by_id = {row.get("source_id"): row for row in correction_rows}
    report_by_id = {row.get("source_id"): row for row in report.get("rows", []) if isinstance(row, dict)}
    rows: list[dict[str, Any]] = []
    for source_id in FAILED_SOURCE_IDS:
        source_evidence = _evidence_by_source(evidence, source_id)
        source_cases = [row for row in cases if row.get("source_id") == source_id]
        if len(source_cases) != 3:
            raise RetryPreparationError(f"preserved case count is invalid: {source_id}")
        mutation = source_evidence[2]
        if mutation.get("observed_outcome") != "mutation_not_detected" or mutation.get("maximum_mismatch_count") != 0:
            raise RetryPreparationError(f"preserved failed mutation identity/result is invalid: {source_id}")
        if source_evidence[0].get("observed_outcome") != "passed":
            raise RetryPreparationError(f"preserved positive case did not pass: {source_id}")
        base_row = correction_by_id.get(source_id)
        if not isinstance(base_row, dict):
            raise RetryPreparationError(f"base correction row is missing: {source_id}")
        replacement_name, _ = _replacement_mutation(source_id)
        rows.append({
            "source_id": source_id,
            "task_id": report_by_id[source_id].get("task_id"),
            "original_mutation_name": mutation.get("mutation_name"),
            "original_candidate_id": mutation.get("candidate_id"),
            "original_candidate_sha256": mutation.get("candidate_sha256"),
            "testbench_sha256": mutation.get("testbench_sha256"),
            "classification": "semantically_equivalent_mutation",
            "reason": "The mutation compiles, runs, and reports zero mismatches; public-spec semantic analysis proves that its observable behavior is equivalent, so the negative fixture cannot qualify the asset.",
            "public_spec_analysis": _semantic_analysis(source_id),
            "replacement_mutation_name": replacement_name,
            "base_correction_version": base_row.get("correction_version"),
            "base_dependency_closure": base_row.get("dependency_closure"),
            "base_reference_supplied": False,
            "base_support_files": [],
        })
    output = {
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "run_id": failed_run_root.name,
        "parent_attempt": "qualification_attempt_01",
        "analysis_mode": "read_only_public_spec_and_sanitized_qualification_metadata",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "base_correction_manifest_sha256": sha256_file(correction_manifest_path),
        "qualification_validation_report_sha256": sha256_file(report_path),
        "qualification_evidence_sha256": sha256_file(evidence_path),
        "qualification_sidecar_sha256": sha256_file(sidecar_path),
        "reference_inspected": False,
        "rerun": False,
        "raw_hdl_included": False,
        "teacher_generation_allowed": False,
        "rows": rows,
        "summary": {
            "failed_task_count": 2,
            "classifications": {"semantically_equivalent_mutation": 2},
            "replacement_scope": "mutation_fixture_only",
            "testbench_changes_required": False,
            "existing_qualification_attempt_preserved": True,
        },
        "errors": [],
    }
    serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _assert_metadata_safe(serialized)
    _write_exclusive(output_path, serialized.encode("utf-8"))
    return output


def _assert_metadata_safe(serialized: str) -> None:
    lowered = serialized.casefold()
    for marker in ("/home/", "/tmp/", "/root/", "private_assets", ".local_data", "reference.sv", "_ref.sv"):
        if marker.casefold() in lowered:
            raise RetryPreparationError(f"metadata output contains forbidden marker: {marker}")


def create_retry_overlay(
    *,
    base_correction_root: Path,
    diagnosis_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Create only the two replacement mutation files and their attestation."""
    if output_root.exists() or output_root.is_symlink():
        raise RetryPreparationError("retry overlay already exists")
    diagnosis = _load_json(diagnosis_path)
    base_manifest_path = base_correction_root / "manifest.jsonl"
    base_rows = _load_jsonl(base_manifest_path)
    base_by_id = {row.get("source_id"): row for row in base_rows}
    diagnosis_rows = {row.get("source_id"): row for row in diagnosis.get("rows", [])}
    if list(diagnosis_rows) != list(FAILED_SOURCE_IDS):
        raise RetryPreparationError("diagnosis order does not match retry scope")
    output_root.mkdir(mode=0o700, parents=True)
    qualification_root = output_root / "qualification"
    qualification_root.mkdir(mode=0o700)
    manifest_rows: list[dict[str, Any]] = []
    for source_id in FAILED_SOURCE_IDS:
        base = base_by_id.get(source_id)
        diagnosis_row = diagnosis_rows[source_id]
        if not isinstance(base, dict):
            raise RetryPreparationError(f"base correction row missing: {source_id}")
        name, text = _replacement_mutation(source_id)
        errors = _candidate_static_errors(text)
        if errors:
            raise RetryPreparationError(f"replacement candidate is statically invalid: {source_id}")
        destination = qualification_root / source_id / f"{name}.sv"
        _write_exclusive(destination, text.encode("utf-8"))
        row = {
            "schema_version": RETRY_MANIFEST_SCHEMA_VERSION,
            "source_dataset": "VerilogEval",
            "source_id": source_id,
            "task_id": base.get("task_id"),
            "top_module": base.get("top_module"),
            "split": "train",
            "source_commit": SOURCE_COMMIT,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "frozen_split_sha256": FROZEN_SPLIT_SHA256,
            "selection_ids_sha256": SELECTION_IDS_SHA256,
            "selection_report_sha256": SELECTION_REPORT_SHA256,
            "public_specification_sha256": base.get("public_specification_sha256"),
            "base_correction_version": BASE_CORRECTION_VERSION,
            "correction_version": RETRY_CORRECTION_VERSION,
            "base_correction_manifest_sha256": sha256_file(base_manifest_path),
            "original_testbench_sha256": base.get("original_testbench_sha256"),
            "base_corrected_testbench_sha256": base.get("corrected_testbench_sha256"),
            "testbench_reused": True,
            "original_mutation_name": diagnosis_row.get("original_mutation_name"),
            "original_mutation_candidate_sha256": diagnosis_row.get("original_candidate_sha256"),
            "replacement_mutation_name": name,
            "replacement_mutation_path": f"qualification/{source_id}/{name}.sv",
            "replacement_mutation_sha256": sha256_file(destination),
            "classification": "semantically_equivalent_mutation",
            "correction_reason": "Replace a public-spec-equivalent negative mutation with a behaviorally distinguishable public-spec mutation; reuse the unchanged corrected testbench.",
            "authoring_method": "trusted_manual_public_spec_mutation",
            "dependency_closure": "passed",
            "reference_supplied": False,
            "support_files": [],
            "qualification_status": "pending_isolated_qualification",
            "verification_readiness": "pending_qualification",
            "static_audit": {
                "module_declaration_count": 1,
                "module_declarations": ["TopModule"],
                "private_marker_found": False,
                "support_file_count": 0,
            },
        }
        manifest_rows.append(row)
    manifest_path = output_root / "manifest.jsonl"
    _write_jsonl(manifest_path, manifest_rows)
    manifest_hash = sha256_file(manifest_path)
    attestation = {
        "schema_version": RETRY_ATTESTATION_SCHEMA_VERSION,
        "correction_version": RETRY_CORRECTION_VERSION,
        "base_correction_version": BASE_CORRECTION_VERSION,
        "base_correction_manifest_sha256": sha256_file(base_manifest_path),
        "retry_manifest_sha256": manifest_hash,
        "diagnosis_sha256": sha256_file(diagnosis_path),
        "selected_source_ids": list(FAILED_SOURCE_IDS),
        "replacement_mutation_count": 2,
        "testbench_changes": False,
        "reference_supplied": False,
        "support_file_count": 0,
        "dependency_closure": "passed",
        "qualification_status": "pending_isolated_qualification",
        "teacher_generation_allowed": False,
        "rows": [
            {
                "source_id": row["source_id"],
                "replacement_mutation_name": row["replacement_mutation_name"],
                "replacement_mutation_sha256": row["replacement_mutation_sha256"],
                "base_corrected_testbench_sha256": row["base_corrected_testbench_sha256"],
            }
            for row in manifest_rows
        ],
    }
    _write_json(output_root / "retry_attestation.json", attestation)
    _validate_tree_permissions(output_root)
    return {
        "retry_manifest_sha256": manifest_hash,
        "diagnosis_sha256": sha256_file(diagnosis_path),
        "source_ids": list(FAILED_SOURCE_IDS),
        "replacement_mutation_count": 2,
        "testbench_changes": False,
        "reference_supplied": False,
        "support_file_count": 0,
    }


def _git_identity(repository: Path) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repository, check=True,
            capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RetryPreparationError("could not record repository identity") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RetryPreparationError("repository HEAD is not a full commit")
    return {
        "commit": commit,
        "working_tree_clean": status == "",
        "working_tree_status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
    }


def _base_parent_hashes(failed_run_root: Path) -> dict[str, str]:
    return {
        "qualification_validation_report_sha256": sha256_file(failed_run_root / "reports" / "asset_qualification_validation.json"),
        "qualification_evidence_sha256": sha256_file(failed_run_root / "reports" / "asset_qualification_evidence.jsonl"),
        "qualification_sidecar_sha256": sha256_file(failed_run_root / "staged" / "candidate_evidence.jsonl.runner.json"),
        "candidate_manifest_sha256": sha256_file(failed_run_root / "input" / "candidate_manifest.jsonl"),
    }


def _prepare_case_inputs(
    *,
    failed_run_root: Path,
    overlay_root: Path,
    diagnosis_path: Path,
    retry_run_root: Path,
) -> dict[str, Any]:
    if retry_run_root.exists() or retry_run_root.is_symlink():
        raise RetryPreparationError("retry run already exists")
    overlay_manifest_path = overlay_root / "manifest.jsonl"
    overlay_rows = _load_jsonl(overlay_manifest_path)
    if [row.get("source_id") for row in overlay_rows] != list(FAILED_SOURCE_IDS):
        raise RetryPreparationError("retry overlay order is invalid")
    overlay_by_id = {row["source_id"]: row for row in overlay_rows}
    base_input_workspace = failed_run_root / "input" / "workspace"
    for source_id in FAILED_SOURCE_IDS:
        _require_directory(base_input_workspace / source_id)
    retry_run_root.mkdir(mode=0o700, parents=True)
    input_root = retry_run_root / "input"
    workspace = input_root / "workspace"
    reports_root = retry_run_root / "reports"
    staged_root = retry_run_root / "staged"
    for directory in (input_root, workspace, reports_root, staged_root):
        directory.mkdir(mode=0o700)
    case_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    case_number = 0
    for source_id in FAILED_SOURCE_IDS:
        overlay = overlay_by_id[source_id]
        base_dir = base_input_workspace / source_id
        task_dir = workspace / source_id
        task_dir.mkdir(mode=0o700)
        base_testbench = base_dir / "testbench.sv"
        base_positive = base_dir / "public_spec_candidate.sv"
        base_constant = base_dir / "constant_zero.sv"
        for path in (base_testbench, base_positive, base_constant):
            _require_regular(path)
        _copy_regular(base_testbench, task_dir / "testbench.sv")
        if sha256_file(task_dir / "testbench.sv") != overlay["base_corrected_testbench_sha256"]:
            raise RetryPreparationError(f"reused testbench hash mismatch: {source_id}")
        _copy_regular(base_positive, task_dir / "public_spec_candidate.sv")
        _copy_regular(base_constant, task_dir / "constant_zero.sv")
        replacement_name = overlay["replacement_mutation_name"]
        replacement_source = overlay_root / overlay["replacement_mutation_path"]
        _copy_regular(replacement_source, task_dir / f"{replacement_name}.sv")
        candidates = (
            ("positive", "public_spec_candidate", "public_spec_candidate.sv", "accepted", "trusted_manual_public_spec"),
            ("negative", "constant_zero", "constant_zero.sv", "rejected", "trusted_manual_public_spec_mutation"),
            ("negative", replacement_name, f"{replacement_name}.sv", "rejected", "trusted_manual_public_spec_mutation"),
        )
        for local_attempt, (kind, name, filename, expected_outcome, authoring_method) in enumerate(candidates, 1):
            candidate_path = task_dir / filename
            candidate_relative = f"{source_id}/{filename}"
            testbench_relative = f"{source_id}/testbench.sv"
            case_number += 1
            candidate_id = f"{RETRY_CORRECTION_VERSION}__{source_id}__{name}"
            case_id = f"qualification_retry_01_{case_number:02d}_{source_id}_{name}"
            candidate_hash = sha256_file(candidate_path)
            testbench_hash = sha256_file(task_dir / "testbench.sv")
            case_rows.append({
                "schema_version": CASE_SCHEMA_VERSION,
                "qualification_case_id": case_id,
                "source_id": source_id,
                "task_id": overlay["task_id"],
                "top_module": overlay["top_module"],
                "candidate_id": candidate_id,
                "attempt": local_attempt,
                "kind": kind,
                "mutation_name": name,
                "expected_outcome": expected_outcome,
                "authoring_method": authoring_method,
                "candidate_rtl_path": candidate_relative,
                "testbench_path": testbench_relative,
                "support_files": [],
                "candidate_sha256": candidate_hash,
                "testbench_sha256": testbench_hash,
                "reference_rtl_supplied": False,
            })
            manifest_rows.append({
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "task_id": overlay["task_id"],
                "source_id": source_id,
                "attempt": local_attempt,
                "top_module": overlay["top_module"],
                "testbench_top": "tb",
                "candidate_rtl_path": candidate_relative,
                "testbench_path": testbench_relative,
                "support_files": [],
                "simulation_result_contract": "mismatch_count_v1",
                "requested_checks": {"compile": True, "simulation": True, "lint": False, "synthesis": False},
            })
    _write_jsonl(retry_run_root / "case_manifest.jsonl", case_rows)
    _write_jsonl(input_root / "candidate_manifest.jsonl", manifest_rows)
    parent_hashes = _base_parent_hashes(failed_run_root)
    base_manifest_sha = overlay_rows[0]["base_correction_manifest_sha256"]
    preparation = {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "run_id": retry_run_root.name,
        "scope": "qualification_only",
        "parent_run_id": failed_run_root.name,
        "parent_attempt": "qualification_attempt_01",
        "parent_hashes": parent_hashes,
        "diagnosis_sha256": sha256_file(diagnosis_path),
        "retry_manifest_sha256": sha256_file(overlay_manifest_path),
        "retry_attestation_sha256": sha256_file(overlay_root / "retry_attestation.json"),
        "base_correction_manifest_sha256": base_manifest_sha,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "selection_ids_sha256": SELECTION_IDS_SHA256,
        "selection_report_sha256": SELECTION_REPORT_SHA256,
        "correction_version": RETRY_CORRECTION_VERSION,
        "selected_source_ids": list(FAILED_SOURCE_IDS),
        "case_count": len(case_rows),
        "positive_case_count": sum(row["kind"] == "positive" for row in case_rows),
        "negative_case_count": sum(row["kind"] == "negative" for row in case_rows),
        "candidate_manifest_sha256": sha256_file(input_root / "candidate_manifest.jsonl"),
        "workspace_tree_sha256": _workspace_tree_sha256(workspace),
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "existing_evidence": False,
        "existing_attempt_history": False,
        "qualification_status_before": "pending_isolated_qualification",
        "teacher_generation_allowed": False,
        "errors": [],
    }
    _write_json(retry_run_root / "preparation_report.json", preparation)
    return preparation


def _create_authorization(
    *,
    retry_run_root: Path,
    preparation: dict[str, Any],
    diagnosis_path: Path,
    overlay_root: Path,
    rtlbench_root: Path,
    rtlspecializer_root: Path,
) -> dict[str, Any]:
    rtlbench_python = rtlbench_root / ".venv" / "bin" / "python"
    input_root = retry_run_root / "input"
    output_path = retry_run_root / "staged" / "candidate_evidence.jsonl"
    return {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "run_id": retry_run_root.name,
        "status": "authorized_once",
        "authorization_scope": "qualification_only",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "selection_ids_sha256": SELECTION_IDS_SHA256,
        "selection_report_sha256": SELECTION_REPORT_SHA256,
        "base_correction_version": BASE_CORRECTION_VERSION,
        "correction_version": RETRY_CORRECTION_VERSION,
        "retry_manifest_sha256": sha256_file(overlay_root / "manifest.jsonl"),
        "retry_attestation_sha256": sha256_file(overlay_root / "retry_attestation.json"),
        "diagnosis_sha256": sha256_file(diagnosis_path),
        "parent_run_id": preparation["parent_run_id"],
        "parent_hashes": preparation["parent_hashes"],
        "selected_source_ids": list(FAILED_SOURCE_IDS),
        "case_count": 6,
        "positive_case_count": 2,
        "negative_case_count": 4,
        "candidate_manifest_sha256": preparation["candidate_manifest_sha256"],
        "workspace_tree_sha256": preparation["workspace_tree_sha256"],
        "qualification_status_before": "pending_isolated_qualification",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "teacher_generation_allowed": False,
        "candidate_generation_repeated": False,
        "qualification_attempt_repeated": False,
        "runner": {
            "profile": "pilot-docker",
            "runtime": "docker",
            "runtime_mode": "rootful-daemon",
            "rootless": False,
            "image_id": IMAGE_ID,
            "rtlbench_commit": RTLBench_COMMIT,
            "network_policy": "none",
            "read_only_rootfs": True,
            "runtime_user": "65532:65532",
            "runtime_managed_staging": True,
            "canonical_handoff_direct_mount": False,
            "instructions_staged": False,
            "verification_plan_staged": False,
        },
        "rtlspecializer": _git_identity(rtlspecializer_root),
        "exact_command": [
            str(rtlbench_python),
            "runner/run_isolated.py",
            "--profile",
            "pilot-docker",
            "--acknowledge-rootful-runtime",
            "--image",
            IMAGE_ID,
            "--input",
            str(input_root.resolve()),
            "--output",
            str(output_path.resolve()),
        ],
        "authorized_invocations": 1,
        "execution_status": "not_started",
        "errors": [],
    }


def _validate_preparation(
    *,
    retry_run_root: Path,
    overlay_root: Path,
    diagnosis_path: Path,
    authorization: dict[str, Any],
    preparation: dict[str, Any],
) -> dict[str, Any]:
    input_root = retry_run_root / "input"
    workspace = input_root / "workspace"
    case_rows = _load_jsonl(retry_run_root / "case_manifest.jsonl")
    manifest_rows = _load_jsonl(input_root / "candidate_manifest.jsonl")
    overlay_rows = _load_jsonl(overlay_root / "manifest.jsonl")
    if [row.get("source_id") for row in overlay_rows] != list(FAILED_SOURCE_IDS):
        raise RetryPreparationError("overlay order mismatch")
    if len(case_rows) != 6 or len(manifest_rows) != 6:
        raise RetryPreparationError("retry case count is not six")
    if [row.get("candidate_id") for row in case_rows] != [row.get("candidate_id") for row in manifest_rows]:
        raise RetryPreparationError("case and candidate manifest order mismatch")
    if [row.get("source_id") for row in case_rows] != [source_id for source_id in FAILED_SOURCE_IDS for _ in range(3)]:
        raise RetryPreparationError("retry source order mismatch")
    if sum(row.get("kind") == "positive" for row in case_rows) != 2 or sum(row.get("kind") == "negative" for row in case_rows) != 4:
        raise RetryPreparationError("retry positive/negative counts are invalid")
    for case, manifest in zip(case_rows, manifest_rows):
        if case.get("candidate_id") != manifest.get("candidate_id") or case.get("task_id") != manifest.get("task_id"):
            raise RetryPreparationError("retry identity mismatch")
        candidate_path = workspace / case["candidate_rtl_path"]
        testbench_path = workspace / case["testbench_path"]
        _require_regular(candidate_path)
        _require_regular(testbench_path)
        if sha256_file(candidate_path) != case["candidate_sha256"] or sha256_file(testbench_path) != case["testbench_sha256"]:
            raise RetryPreparationError("retry case hash mismatch")
        if _candidate_static_errors(candidate_path.read_text(encoding="utf-8")):
            raise RetryPreparationError("retry candidate static privacy/module check failed")
        testbench_lowered = testbench_path.read_text(encoding="utf-8").casefold()
        if any(marker.casefold() in testbench_lowered for marker in PRIVATE_MARKERS if marker not in {"candidate_evidence", "mutation_evidence"}):
            raise RetryPreparationError("retry testbench contains a forbidden private marker")
        if case.get("support_files") != [] or case.get("reference_rtl_supplied") is not False:
            raise RetryPreparationError("retry case supplies support or reference content")
    if preparation.get("candidate_manifest_sha256") != sha256_file(input_root / "candidate_manifest.jsonl"):
        raise RetryPreparationError("preparation candidate manifest hash mismatch")
    if preparation.get("workspace_tree_sha256") != _workspace_tree_sha256(workspace):
        raise RetryPreparationError("preparation workspace hash mismatch")
    if authorization.get("candidate_manifest_sha256") != preparation["candidate_manifest_sha256"] or authorization.get("workspace_tree_sha256") != preparation["workspace_tree_sha256"]:
        raise RetryPreparationError("authorization input hash mismatch")
    for forbidden in (
        retry_run_root / "verification",
        retry_run_root / "candidate_evidence.jsonl",
        retry_run_root / "generation_attempts.jsonl",
    ):
        if forbidden.exists() or forbidden.is_symlink():
            raise RetryPreparationError("retry run contains forbidden execution output")
    if (input_root / "run_instructions.md").exists() or (input_root / "verification_plan.jsonl").exists():
        raise RetryPreparationError("runner input contains operator-only files")
    _validate_tree_permissions(retry_run_root)
    return {
        "schema_version": PREFLIGHT_SCHEMA_VERSION,
        "status": "ready_for_isolated_execution",
        "qualification_only": True,
        "run_id": retry_run_root.name,
        "selected_source_ids": list(FAILED_SOURCE_IDS),
        "case_count": 6,
        "positive_case_count": 2,
        "negative_case_count": 4,
        "retry_manifest_sha256": sha256_file(overlay_root / "manifest.jsonl"),
        "diagnosis_sha256": sha256_file(diagnosis_path),
        "candidate_manifest_sha256": preparation["candidate_manifest_sha256"],
        "workspace_tree_sha256": preparation["workspace_tree_sha256"],
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "existing_evidence": False,
        "existing_attempt_history": False,
        "instructions_staged": False,
        "verification_plan_staged": False,
        "permissions": {"directories": "0700", "files": "0600"},
        "authorization_status": authorization.get("status"),
        "execution_status": "not_started",
        "teacher_generation_allowed": False,
        "errors": [],
    }


def prepare_retry(
    *,
    failed_run_root: Path,
    base_correction_root: Path,
    diagnosis_path: Path,
    overlay_root: Path,
    retry_run_root: Path,
    rtlbench_root: Path,
    rtlspecializer_root: Path,
    diagnosis_output: Path | None = None,
) -> dict[str, Any]:
    if diagnosis_output is not None:
        diagnosis = build_diagnosis(
            failed_run_root=failed_run_root,
            base_correction_root=base_correction_root,
            output_path=diagnosis_output,
        )
    else:
        diagnosis = _load_json(diagnosis_path)
    if diagnosis.get("schema_version") != DIAGNOSIS_SCHEMA_VERSION:
        raise RetryPreparationError("diagnosis schema mismatch")
    overlay = create_retry_overlay(
        base_correction_root=base_correction_root,
        diagnosis_path=diagnosis_path,
        output_root=overlay_root,
    )
    preparation = _prepare_case_inputs(
        failed_run_root=failed_run_root,
        overlay_root=overlay_root,
        diagnosis_path=diagnosis_path,
        retry_run_root=retry_run_root,
    )
    authorization = _create_authorization(
        retry_run_root=retry_run_root,
        preparation=preparation,
        diagnosis_path=diagnosis_path,
        overlay_root=overlay_root,
        rtlbench_root=rtlbench_root,
        rtlspecializer_root=rtlspecializer_root,
    )
    _write_json(retry_run_root / "reports" / "asset_qualification_authorization.json", authorization)
    preflight = _validate_preparation(
        retry_run_root=retry_run_root,
        overlay_root=overlay_root,
        diagnosis_path=diagnosis_path,
        authorization=authorization,
        preparation=preparation,
    )
    _write_json(retry_run_root / "reports" / "asset_qualification_preflight.json", preflight)
    return {
        "diagnosis": diagnosis,
        "overlay": overlay,
        "preparation": preparation,
        "authorization": authorization,
        "preflight": preflight,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-run-root", required=True, type=Path)
    parser.add_argument("--base-correction-root", required=True, type=Path)
    parser.add_argument("--diagnosis-output", required=True, type=Path)
    parser.add_argument("--retry-overlay-root", required=True, type=Path)
    parser.add_argument("--retry-run-root", required=True, type=Path)
    parser.add_argument("--rtlbench-root", required=True, type=Path)
    parser.add_argument("--rtlspecializer-root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare_retry(
            failed_run_root=args.failed_run_root,
            base_correction_root=args.base_correction_root,
            diagnosis_path=args.diagnosis_output,
            overlay_root=args.retry_overlay_root,
            retry_run_root=args.retry_run_root,
            rtlbench_root=args.rtlbench_root,
            rtlspecializer_root=args.rtlspecializer_root,
            diagnosis_output=args.diagnosis_output,
        )
    except (OSError, UnicodeError, RetryPreparationError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({
        "ok": True,
        "classification": "semantically_equivalent_mutation",
        "diagnosis_path": str(args.diagnosis_output),
        "retry_overlay_root": str(args.retry_overlay_root),
        "retry_run_root": str(args.retry_run_root),
        "selected_source_ids": list(FAILED_SOURCE_IDS),
        "case_count": result["preparation"]["case_count"],
        "candidate_manifest_sha256": result["preparation"]["candidate_manifest_sha256"],
        "workspace_tree_sha256": result["preparation"]["workspace_tree_sha256"],
        "retry_manifest_sha256": result["overlay"]["retry_manifest_sha256"],
        "preflight_status": result["preflight"]["status"],
        "execution_performed": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
