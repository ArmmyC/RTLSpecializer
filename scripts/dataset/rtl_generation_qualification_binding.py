"""Bind a passed asset-qualification retry to teacher-generation inputs.

This module creates a private, derived asset view for teacher generation.  It
never edits the assembled task/asset manifests, the original private asset
view, or a failed qualification report.  The derived view replaces only the
testbench bytes with the passed retry overlay and records the complete hash
chain in a separate binding report.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any


SCHEMA_VERSION = "rtl_generation_qualification_binding_v0.1"
CORRECTION_VERSION = "assetfix_v002"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
QUALIFICATION_SOURCE = "asset_qualification_retry_01"
EXPECTED_SOURCE_IDS = (
    "Prob001_zero",
    "Prob020_mt2015_eq2",
    "Prob071_always_casez",
    "Prob048_m2014_q4c",
    "Prob079_fsm3onehot",
)
EXPECTED_TASK_IDS = (
    "rtlgen_verilogeval_prob001_zero_eaec15c148f9",
    "rtlgen_verilogeval_prob020_mt2015_eq2_aec940375a36",
    "rtlgen_verilogeval_prob071_always_casez_35df92a35797",
    "rtlgen_verilogeval_prob048_m2014_q4c_7379fc4191bf",
    "rtlgen_verilogeval_prob079_fsm3onehot_e3cf6755e024",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BindingError(ValueError):
    """Raised when a qualification binding cannot be proven."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise BindingError(f"missing or symlinked JSON input: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BindingError(f"invalid JSON input: {path}") from exc


def _read_jsonl_with_lines(
    path: Path,
    *,
    identity_keys: tuple[str, ...] = ("task_id", "source_id"),
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if path.is_symlink() or not path.is_file():
        raise BindingError(f"missing or symlinked JSONL input: {path}")
    rows: list[dict[str, Any]] = []
    raw_by_id: dict[str, bytes] = {}
    for line_number, raw_line in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BindingError(f"invalid JSONL row {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise BindingError(f"JSONL row is not an object: {path}:{line_number}")
        identity = next((row.get(key) for key in identity_keys if row.get(key) is not None), None)
        if not isinstance(identity, str) or identity in raw_by_id:
            raise BindingError(f"duplicate or missing row identity: {path}:{line_number}")
        rows.append(row)
        raw_by_id[identity] = raw_line
    return rows, raw_by_id


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise BindingError(f"{label} is not a SHA-256 hash")
    return value


def _require_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise BindingError(f"{label} is missing, symlinked, or not a file")


def _safe_relative(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise BindingError(f"{label} is not a safe relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BindingError(f"{label} contains an unsafe path component")
    path = root.joinpath(*parts)
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise BindingError(f"{label} escapes its private root")
    return path


def _write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise BindingError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_qualification(
    *,
    run_root: Path,
    retry_root: Path,
    tasks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    qualification_report_path: Path,
    qualification_manifest_path: Path,
    evidence_path: Path,
    sidecar_path: Path,
    correction_manifest_path: Path,
) -> dict[str, Any]:
    expected_ids = list(EXPECTED_SOURCE_IDS)
    if [row.get("source_id") for row in tasks] != expected_ids:
        raise BindingError("task rows do not match the fixed five-task order")
    if [row.get("source_id") for row in assets] != expected_ids:
        raise BindingError("asset rows do not match the fixed five-task order")
    if len(tasks) != 5 or len(assets) != 5:
        raise BindingError("teacher generation requires exactly five tasks and assets")

    report = _read_json(qualification_report_path)
    if report.get("schema_version") != "rtl_verification_asset_qualification_v0.1":
        raise BindingError("qualification report has the wrong schema")
    if report.get("qualification_passed") is not True:
        raise BindingError("retry qualification report is not passed")
    if report.get("source_commit") != SOURCE_COMMIT:
        raise BindingError("qualification source commit mismatch")
    if report.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise BindingError("qualification source-tree hash mismatch")
    if report.get("frozen_split_sha256") != FROZEN_SPLIT_SHA256:
        raise BindingError("qualification frozen-split hash mismatch")
    if report.get("correction_version") != CORRECTION_VERSION:
        raise BindingError("qualification correction version mismatch")
    if report.get("row_count") != 5 or report.get("mutation_count") != 10:
        raise BindingError("qualification report does not cover five rows and ten mutations")
    report_rows = report.get("rows")
    if not isinstance(report_rows, list) or [row.get("source_id") for row in report_rows] != expected_ids:
        raise BindingError("qualification report row order does not match the selected tasks")
    if any(row.get("qualification_passed") is not True for row in report_rows):
        raise BindingError("a qualification report row is not passed")

    correction_rows, _ = _read_jsonl_with_lines(correction_manifest_path)
    correction_by_id = {row.get("source_id"): row for row in correction_rows}
    if set(correction_by_id) != set(expected_ids):
        raise BindingError("retry correction manifest IDs do not match the five tasks")
    if any(row.get("correction_version") != CORRECTION_VERSION for row in correction_rows):
        raise BindingError("retry correction manifest has a version mismatch")

    qualification_rows, _ = _read_jsonl_with_lines(qualification_manifest_path, identity_keys=("mutation_id",))
    evidence_rows, _ = _read_jsonl_with_lines(evidence_path, identity_keys=("mutation_id",))
    if len(qualification_rows) != 10 or len(evidence_rows) != 10:
        raise BindingError("retry qualification must contain ten mutation rows")
    evidence_by_id = {row.get("mutation_id"): row for row in evidence_rows}
    if len(evidence_by_id) != 10:
        raise BindingError("retry qualification evidence has duplicate mutation IDs")
    mutation_counts: dict[str, int] = {source_id: 0 for source_id in expected_ids}
    detected_counts: dict[str, int] = {source_id: 0 for source_id in expected_ids}
    for mutation in qualification_rows:
        mutation_id = mutation.get("mutation_id")
        evidence = evidence_by_id.get(mutation_id)
        source_id = mutation.get("source_id")
        if source_id not in mutation_counts or evidence is None:
            raise BindingError("retry mutation manifest/evidence identity mismatch")
        mutation_counts[source_id] += 1
        checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
        simulation = checks.get("simulation") if isinstance(checks.get("simulation"), dict) else {}
        if evidence.get("failure_category") != "passed" or simulation.get("mutated_detects_mutation", {}).get("passed") is not True:
            raise BindingError(f"retry mutation was not detected: {mutation_id}")
        detected_counts[source_id] += 1
    if any(value < 1 for value in mutation_counts.values()) or mutation_counts != detected_counts:
        raise BindingError("every task must have at least one detected retry mutation")

    sidecar = _read_json(sidecar_path)
    if sidecar.get("profile") != "pilot-docker" or sidecar.get("runtime") != "docker":
        raise BindingError("retry qualification sidecar is not pilot-docker")
    if sidecar.get("rtlbench_commit") != "fcad47eb03e469097432229e1285b9239fd23a00":
        raise BindingError("retry qualification RTLBench commit mismatch")
    if sidecar.get("network_policy") != "none" or sidecar.get("partial_evidence_sha256") is not None:
        raise BindingError("retry qualification sidecar is not an isolated complete run")
    if sidecar.get("evidence_sha256") != _sha256_file(evidence_path):
        raise BindingError("retry qualification sidecar evidence hash mismatch")
    if report.get("qualification_evidence_sha256") != _sha256_file(evidence_path):
        raise BindingError("qualification report evidence hash mismatch")
    if report.get("qualification_sidecar_sha256") != _sha256_file(sidecar_path):
        raise BindingError("qualification report sidecar hash mismatch")
    if report.get("qualification_manifest_sha256") != _sha256_file(qualification_manifest_path):
        raise BindingError("qualification report mutation-manifest hash mismatch")

    failed_report = run_root / "reports" / "asset_qualification.json"
    _require_file(failed_report, "original failed qualification report")
    failure_analysis = run_root / "reports" / "asset_qualification_failure_analysis.json"
    _require_file(failure_analysis, "original qualification failure analysis")

    return {
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "correction_version": CORRECTION_VERSION,
        "qualification_source": QUALIFICATION_SOURCE,
        "qualification_report": report,
        "correction_by_id": correction_by_id,
        "qualification_manifest_sha256": _sha256_file(qualification_manifest_path),
        "qualification_evidence_sha256": _sha256_file(evidence_path),
        "qualification_sidecar_sha256": _sha256_file(sidecar_path),
        "correction_manifest_sha256": _sha256_file(correction_manifest_path),
        "failed_qualification_report_sha256": _sha256_file(failed_report),
        "failure_analysis_sha256": _sha256_file(failure_analysis),
        "mutation_counts": mutation_counts,
        "detected_counts": detected_counts,
    }


def bind_qualification_retry(
    *,
    run_root: Path,
    retry_root: Path,
    output_root: Path,
    binding_output: Path,
) -> tuple[dict[str, Any], int]:
    stage: Path | None = None
    try:
        tasks_path = run_root / "tasks" / "generation_tasks.jsonl"
        assets_path = run_root / "tasks" / "verification_assets.jsonl"
        private_root = run_root / "private_assets"
        qualification_report_path = retry_root / "qualification_validation_report.json"
        qualification_manifest_path = retry_root / "input" / "mutation_manifest.jsonl"
        evidence_path = retry_root / "qualification-output" / "mutation_evidence.jsonl"
        sidecar_path = retry_root / "qualification-output" / "mutation_evidence.runner.json"
        correction_manifest_path = retry_root / "correction" / "manifest.jsonl"
        tasks, task_lines = _read_jsonl_with_lines(tasks_path)
        assets, asset_lines = _read_jsonl_with_lines(assets_path)
        binding = _validate_qualification(
            run_root=run_root,
            retry_root=retry_root,
            tasks=tasks,
            assets=assets,
            qualification_report_path=qualification_report_path,
            qualification_manifest_path=qualification_manifest_path,
            evidence_path=evidence_path,
            sidecar_path=sidecar_path,
            correction_manifest_path=correction_manifest_path,
        )
        if output_root.exists() or output_root.is_symlink() or binding_output.exists() or binding_output.is_symlink():
            raise BindingError("qualification binding output already exists")

        stage = Path(tempfile.mkdtemp(prefix=".teacher-assets.stage-", dir=str(retry_root)))
        os.chmod(stage, 0o700)
        stage_assets = stage / "verification_assets.jsonl"
        stage_workspace = stage / "workspace"
        stage_workspace.mkdir(mode=0o700)
        derived_assets: list[dict[str, Any]] = []
        binding_rows: list[dict[str, Any]] = []
        asset_by_id = {row["source_id"]: row for row in assets}
        task_by_id = {row["source_id"]: row for row in tasks}
        correction_by_id = binding["correction_by_id"]
        retry_correction_root = retry_root / "correction" / "tasks"
        retry_workspace_root = retry_root / "input" / "workspace"
        for source_id in EXPECTED_SOURCE_IDS:
            task = task_by_id[source_id]
            base_asset = asset_by_id[source_id]
            task_id = task["task_id"]
            source_reference = _safe_relative(private_root, base_asset["reference_rtl_path"], f"reference path {source_id}")
            source_testbench = _safe_relative(private_root, base_asset["testbench_path"], f"testbench path {source_id}")
            retry_testbench = retry_correction_root / source_id / "testbench.sv"
            retry_qualification_testbench = retry_workspace_root / source_id / "testbench.sv"
            _require_file(source_reference, f"base reference RTL {source_id}")
            _require_file(source_testbench, f"base testbench {source_id}")
            _require_file(retry_testbench, f"retry corrected testbench {source_id}")
            _require_file(retry_qualification_testbench, f"retry qualification testbench {source_id}")
            retry_bytes = retry_testbench.read_bytes()
            if retry_bytes != retry_qualification_testbench.read_bytes():
                raise BindingError(f"retry correction and qualification testbench differ: {source_id}")
            retry_hash = _sha256_bytes(retry_bytes)
            if retry_hash != correction_by_id[source_id].get("corrected_testbench_sha256"):
                raise BindingError(f"retry correction hash mismatch: {source_id}")

            destination_dir = stage_workspace / task_id
            destination_dir.mkdir(mode=0o700)
            _write_new(destination_dir / "reference.sv", source_reference.read_bytes())
            _write_new(destination_dir / "testbench.sv", retry_bytes)
            derived_asset = json.loads(json.dumps(base_asset))
            derived_asset["input_hashes"]["testbench_sha256"] = retry_hash
            derived_assets.append(derived_asset)
            binding_rows.append({
                "source_id": source_id,
                "task_id": task_id,
                "qualification_source": QUALIFICATION_SOURCE,
                "qualification_result": "passed",
                "mutation_rows": binding["mutation_counts"][source_id],
                "detected_mutations": binding["detected_counts"][source_id],
                "reference_supplied": False,
                "corrected_testbench_sha256": retry_hash,
                "qualification_report_sha256": _sha256_file(qualification_report_path),
                "mutation_evidence_sha256": binding["qualification_evidence_sha256"],
                "runner_sidecar_sha256": binding["qualification_sidecar_sha256"],
                "task_record_sha256": _sha256_bytes(task_lines[task_id]),
                "asset_record_sha256": None,
                "original_reference_rtl_sha256": base_asset["input_hashes"]["reference_rtl_sha256"],
            })

        manifest_bytes = b"".join(_json_bytes(row) + b"\n" for row in derived_assets)
        _write_new(stage_assets, manifest_bytes)
        for row, binding_row in zip(derived_assets, binding_rows):
            row_bytes = _json_bytes(row) + b"\n"
            binding_row["asset_record_sha256"] = _sha256_bytes(row_bytes)
        binding_report = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_root.name,
            "source_commit": binding["source_commit"],
            "source_tree_sha256": binding["source_tree_sha256"],
            "frozen_split_sha256": binding["frozen_split_sha256"],
            "correction_version": binding["correction_version"],
            "qualification_source": QUALIFICATION_SOURCE,
            "qualification_result": "passed",
            "mutation_rows": 10,
            "detected_mutations": 10,
            "reference_supplied": False,
            "qualification_report_sha256": _sha256_file(qualification_report_path),
            "qualification_manifest_sha256": binding["qualification_manifest_sha256"],
            "mutation_evidence_sha256": binding["qualification_evidence_sha256"],
            "runner_sidecar_sha256": binding["qualification_sidecar_sha256"],
            "correction_manifest_sha256": binding["correction_manifest_sha256"],
            "original_failed_qualification_report_sha256": binding["failed_qualification_report_sha256"],
            "original_failure_analysis_sha256": binding["failure_analysis_sha256"],
            "rows": binding_rows,
            "teacher_private_assets_manifest_sha256": _sha256_file(stage_assets),
            "errors": [],
        }
        output_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(stage, output_root)
        stage = None
        _write_new(binding_output, (_json_bytes(binding_report) + b"\n"))
        return {"ok": True, **binding_report, "teacher_private_assets": str(output_root), "binding_report": str(binding_output)}, 0
    except (BindingError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)


def validate_binding_report(path: Path, expected_source_ids: tuple[str, ...] = EXPECTED_SOURCE_IDS) -> dict[str, Any]:
    report = _read_json(path)
    if report.get("schema_version") != SCHEMA_VERSION:
        raise BindingError("binding report has the wrong schema")
    if (
        report.get("run_id") != "pilot_003_assetfix_v002"
        or report.get("source_commit") != SOURCE_COMMIT
        or report.get("source_tree_sha256") != SOURCE_TREE_SHA256
        or report.get("frozen_split_sha256") != FROZEN_SPLIT_SHA256
        or report.get("correction_version") != CORRECTION_VERSION
        or report.get("qualification_source") != QUALIFICATION_SOURCE
        or report.get("qualification_result") != "passed"
        or report.get("reference_supplied") is not False
    ):
        raise BindingError("binding report is not bound to the pinned passed retry")
    for key in (
        "qualification_report_sha256",
        "qualification_manifest_sha256",
        "mutation_evidence_sha256",
        "runner_sidecar_sha256",
        "correction_manifest_sha256",
        "original_failed_qualification_report_sha256",
        "original_failure_analysis_sha256",
        "teacher_private_assets_manifest_sha256",
    ):
        _require_hash(report.get(key), f"binding {key}")
    if not isinstance(report.get("rows"), list) or len(report["rows"]) != len(expected_source_ids):
        raise BindingError("binding report row count is invalid")
    if tuple(expected_source_ids) == EXPECTED_SOURCE_IDS:
        expected_task_ids = EXPECTED_TASK_IDS
    else:
        expected_task_ids = tuple(str(row.get("task_id")) for row in report["rows"])
    if report.get("mutation_rows") != 10 or report.get("detected_mutations") != 10:
        raise BindingError("binding report does not record ten detected mutations")
    rows = report.get("rows")
    if not isinstance(rows, list) or [row.get("source_id") for row in rows] != list(expected_source_ids):
        raise BindingError("binding row order is invalid")
    for index, row in enumerate(rows):
        if row.get("task_id") != expected_task_ids[index]:
            raise BindingError(f"binding task identity is invalid: {row.get('source_id')}")
        if row.get("qualification_source") != QUALIFICATION_SOURCE or row.get("qualification_result") != "passed":
            raise BindingError(f"binding row is not from the passed retry: {row.get('source_id')}")
        if row.get("correction_version") not in {None, CORRECTION_VERSION}:
            raise BindingError(f"binding row correction version is invalid: {row.get('source_id')}")
        if row.get("reference_supplied") is not False:
            raise BindingError(f"binding row is not passed: {row.get('source_id')}")
        if type(row.get("mutation_rows")) is not int or row["mutation_rows"] < 1 or row.get("detected_mutations") != row["mutation_rows"]:
            raise BindingError(f"binding mutation coverage is invalid: {row.get('source_id')}")
        for key in (
            "corrected_testbench_sha256",
            "qualification_report_sha256",
            "mutation_evidence_sha256",
            "runner_sidecar_sha256",
            "task_record_sha256",
            "asset_record_sha256",
            "original_reference_rtl_sha256",
        ):
            _require_hash(row.get(key), f"binding row {row.get('source_id')} {key}")
        if row["qualification_report_sha256"] != report["qualification_report_sha256"]:
            raise BindingError(f"binding row qualification hash disagrees: {row.get('source_id')}")
        if row["mutation_evidence_sha256"] != report["mutation_evidence_sha256"]:
            raise BindingError(f"binding row evidence hash disagrees: {row.get('source_id')}")
        if row["runner_sidecar_sha256"] != report["runner_sidecar_sha256"]:
            raise BindingError(f"binding row sidecar hash disagrees: {row.get('source_id')}")
    teacher_manifest = path.parent / "teacher_private_assets" / "verification_assets.jsonl"
    if not teacher_manifest.is_file() or teacher_manifest.is_symlink() or _sha256_file(teacher_manifest) != report["teacher_private_assets_manifest_sha256"]:
        raise BindingError("derived teacher-private asset manifest hash does not match binding")
    derived_rows, _ = _read_jsonl_with_lines(teacher_manifest)
    if [row.get("source_id") for row in derived_rows] != list(expected_source_ids):
        raise BindingError("derived teacher-private asset manifest order is invalid")
    for row, derived in zip(rows, derived_rows):
        if derived.get("task_id") != row.get("task_id") or derived.get("source_id") != row.get("source_id"):
            raise BindingError(f"derived asset identity disagrees: {row.get('source_id')}")
        if derived.get("verification_readiness") != "executable_ready" or derived.get("support_files") != []:
            raise BindingError(f"derived asset is not executable-ready: {row.get('source_id')}")
        if derived.get("input_hashes", {}).get("testbench_sha256") != row.get("corrected_testbench_sha256"):
            raise BindingError(f"derived corrected-testbench hash disagrees: {row.get('source_id')}")
    return report


__all__ = [
    "BindingError",
    "CORRECTION_VERSION",
    "EXPECTED_SOURCE_IDS",
    "EXPECTED_TASK_IDS",
    "FROZEN_SPLIT_SHA256",
    "QUALIFICATION_SOURCE",
    "SCHEMA_VERSION",
    "SOURCE_COMMIT",
    "SOURCE_TREE_SHA256",
    "bind_qualification_retry",
    "validate_binding_report",
]
