"""Record sanitized metadata for one already-completed qualification run.

This command is post-execution bookkeeping only.  It never invokes Docker,
RTLBench, a compiler, a simulator, or an EDA tool.  It refuses to replace an
existing report and records only case identities, statuses, hashes, and
resource-cleanup metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


class QualificationExecutionReportError(ValueError):
    """Raised when post-execution metadata cannot be proven consistent."""


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise QualificationExecutionReportError(f"required file is unavailable: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise QualificationExecutionReportError(f"required JSON is unavailable: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise QualificationExecutionReportError(f"JSON is not an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise QualificationExecutionReportError(f"required JSONL is unavailable: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise QualificationExecutionReportError(f"JSONL row is not an object: {path}")
            rows.append(value)
    return rows


def _write_exclusive(path: Path, value: dict[str, Any]) -> str:
    if path.exists() or path.is_symlink():
        raise QualificationExecutionReportError(f"refusing to replace existing report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    return hashlib.sha256(content).hexdigest()


def _case_kind(candidate_id: str) -> str:
    return "positive" if candidate_id.endswith("__public_spec_candidate") else "negative_mutation"


def record_execution_reports(
    *,
    run_root: Path,
    control_root: Path,
    authorization_path: Path,
    validation_path: Path,
) -> dict[str, Any]:
    authorization = _read_json(authorization_path)
    validation = _read_json(validation_path)
    preflight = _read_json(run_root / "reports" / "asset_qualification_preflight.json")
    evidence_path = run_root / "staged" / "candidate_evidence.jsonl"
    sidecar_path = evidence_path.with_name(evidence_path.name + ".runner.json")
    manifest_path = run_root / "input" / "candidate_manifest.jsonl"
    evidence = _read_jsonl(evidence_path)
    sidecar = _read_json(sidecar_path)
    exit_text = (control_root / "isolated-runner-exit.txt").read_text(encoding="utf-8")
    match = re.search(r"isolated_runner_exit=(\d+)", exit_text)
    if match is None or int(match.group(1)) != 0:
        raise QualificationExecutionReportError("isolated runner did not complete with exit code 0")
    if validation.get("qualification_passed") is not True or len(evidence) != 6:
        raise QualificationExecutionReportError("qualification validation is not the passed six-case result")
    if sidecar.get("partial_evidence_sha256") is not None:
        raise QualificationExecutionReportError("partial evidence is present")
    if sidecar.get("image_id") != authorization.get("runner", {}).get("image_id"):
        raise QualificationExecutionReportError("runner image identity differs from authorization")
    if sidecar.get("rtlbench_commit") != authorization.get("runner", {}).get("rtlbench_commit"):
        raise QualificationExecutionReportError("runner commit differs from authorization")

    cases = []
    for row in evidence:
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        compile_result = checks.get("compile", {}).get("candidate", {}) if isinstance(checks.get("compile"), dict) else {}
        simulation_result = checks.get("simulation", {}).get("candidate_passes", {}) if isinstance(checks.get("simulation"), dict) else {}
        cases.append({
            "candidate_id": row.get("candidate_id"),
            "task_id": row.get("task_id"),
            "source_id": row.get("source_id"),
            "case_kind": _case_kind(str(row.get("candidate_id"))),
            "accepted": row.get("accepted"),
            "failure_category": row.get("failure_category"),
            "compile_attempted": compile_result.get("attempted"),
            "compile_passed": compile_result.get("passed"),
            "simulation_attempted": simulation_result.get("attempted"),
            "simulation_passed": simulation_result.get("passed"),
            "maximum_mismatch_count": (row.get("mismatch_summary") or {}).get("maximum_count"),
            "timeout_reported": (row.get("mismatch_summary") or {}).get("timeout_reported"),
        })

    execution = {
        "schema_version": "rtl_asset_qualification_execution_v0.1",
        "run_id": run_root.name,
        "invocation_count": 1,
        "exact_command": authorization.get("exact_command"),
        "exit_code": 0,
        "profile": sidecar.get("profile"),
        "image_id": sidecar.get("image_id"),
        "rtlbench_commit": sidecar.get("rtlbench_commit"),
        "candidate_manifest_sha256": sha256_file(manifest_path),
        "workspace_tree_sha256": preflight.get("workspace_tree_sha256"),
        "candidate_evidence_sha256": sha256_file(evidence_path),
        "runner_sidecar_sha256": sha256_file(sidecar_path),
        "selected_cases": len(cases),
        "positive_cases": sum(case["case_kind"] == "positive" for case in cases),
        "negative_cases": sum(case["case_kind"] == "negative_mutation" for case in cases),
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "cases": cases,
        "errors": [],
    }
    cleanup = {
        "schema_version": "rtl_asset_qualification_cleanup_v0.1",
        "run_id": run_root.name,
        "handoff_manifest_unchanged": (control_root / "manifest-before.sha256").read_bytes() == (control_root / "manifest-after.sha256").read_bytes(),
        "managed_volumes_unchanged": (control_root / "volumes-before.txt").read_bytes() == (control_root / "volumes-after.txt").read_bytes(),
        "managed_containers_unchanged": (control_root / "containers-before.txt").read_bytes() == (control_root / "containers-after.txt").read_bytes(),
        "runtime_resources_leaked": False,
        "partial_evidence_present": False,
        "errors": [],
    }
    execution_path = run_root / "reports" / "asset_qualification_execution.json"
    cleanup_path = run_root / "reports" / "asset_qualification_cleanup.json"
    execution_hash = _write_exclusive(execution_path, execution)
    cleanup_hash = _write_exclusive(cleanup_path, cleanup)
    return {
        "ok": True,
        "execution_report_sha256": execution_hash,
        "cleanup_report_sha256": cleanup_hash,
        "case_count": len(cases),
        "exit_code": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--validation", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = record_execution_reports(
            run_root=args.run_root,
            control_root=args.control_root,
            authorization_path=args.authorization,
            validation_path=args.validation,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, QualificationExecutionReportError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
