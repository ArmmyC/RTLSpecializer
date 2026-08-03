"""Read-only analysis of excluded batch-20 qualification rows.

The analyzer consumes qualification metadata and hashes only.  It never reads
reference RTL, executes a fixture, or edits the preserved qualification
attempt.  When the evidence cannot distinguish a fixture defect from a
positive-fixture or public-spec issue, it deliberately records ``inconclusive``
instead of guessing.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "rtl_asset_qualification_failure_analysis_v0.1"
EXPECTED_FAILED_SOURCES = (
    "Prob092_gatesv100",
    "Prob112_always_case2",
    "Prob115_shift18",
    "Prob109_fsm1",
)
POSITIVE_CLASSIFICATIONS = {
    "positive_fixture_defect",
    "testbench_behavior_defect",
    "testbench_timing_defect",
    "manifest_or_staging_defect",
    "simulation_contract_defect",
    "ambiguous_public_specification",
    "inconclusive",
}
MUTATION_CLASSIFICATIONS = {
    "semantically_equivalent_mutation",
    "mutation_fixture_defect",
    "insufficient_test_stimulus",
    "incorrect_testbench_expectation",
    "staging_or_identity_defect",
    "inconclusive",
}
_FORBIDDEN_SERIALIZED_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
)


class QualificationAnalysisError(ValueError):
    """Raised when the preserved qualification metadata is not trustworthy."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise QualificationAnalysisError(f"required JSON input is not a regular file: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationAnalysisError(f"invalid JSON input: {path.name}") from exc
    if not isinstance(value, dict):
        raise QualificationAnalysisError(f"JSON input is not an object: {path.name}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise QualificationAnalysisError(f"required JSONL input is not a regular file: {path.name}")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise QualificationAnalysisError(f"could not read JSONL input: {path.name}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise QualificationAnalysisError(f"invalid JSONL row {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise QualificationAnalysisError(f"JSONL row is not an object: {path.name}:{line_number}")
        rows.append(value)
    return rows


def _read_ids(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise QualificationAnalysisError(f"required ID list is not a regular file: {path.name}")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _case_summary(case: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    expected = case.get("expected_outcome")
    observed = evidence.get("observed_outcome")
    return {
        "kind": case.get("kind"),
        "mutation_name": case.get("mutation_name"),
        "candidate_id": case.get("candidate_id"),
        "candidate_sha256": case.get("candidate_sha256"),
        "testbench_sha256": case.get("testbench_sha256"),
        "expected_outcome": expected,
        "observed_outcome": observed,
        "compile_passed": evidence.get("compile_passed"),
        "simulation_attempted": evidence.get("simulation_attempted"),
        "simulation_passed": evidence.get("simulation_passed"),
        "reported_mismatch_counts": evidence.get("reported_mismatch_counts"),
        "maximum_mismatch_count": evidence.get("maximum_mismatch_count"),
        "timeout_reported": evidence.get("timeout_reported"),
        "failure_category": evidence.get("failure_category"),
    }


def _classify_positive(cases: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> tuple[str, str]:
    positive = next((row for row in evidence if row.get("kind") == "positive"), None)
    if positive is None:
        return "manifest_or_staging_defect", "No positive evidence row was present for the failed task."
    if not positive.get("compile_passed"):
        return "manifest_or_staging_defect", "The positive fixture did not compile; the preserved metadata cannot distinguish staging from fixture syntax."
    if not positive.get("simulation_attempted"):
        return "simulation_contract_defect", "Compilation completed but the positive simulation was not attempted."
    if positive.get("timeout_reported"):
        return "testbench_timing_defect", "The positive case timed out after compilation."
    negatives = [row for row in evidence if row.get("kind") == "negative"]
    negatives_detected = all(
        row.get("observed_outcome") == "mutation_detected"
        and isinstance(row.get("maximum_mismatch_count"), int)
        and row.get("maximum_mismatch_count") > 0
        for row in negatives
    )
    if negatives_detected and positive.get("simulation_passed") is False:
        return (
            "inconclusive",
            "The positive public-spec fixture compiled and simulated, while both negative mutations were detected; without using reference RTL, the evidence cannot distinguish a positive fixture defect from a testbench expectation or timing defect.",
        )
    return (
        "inconclusive",
        "The preserved evidence does not isolate the cause of the positive mismatch without inspecting private reference behavior.",
    )


def _classify_mutation(evidence: list[dict[str, Any]]) -> tuple[str, str]:
    undetected = [
        row for row in evidence
        if row.get("kind") == "negative" and row.get("observed_outcome") == "mutation_not_detected"
    ]
    if not undetected:
        return "inconclusive", "No undetected mutation row was present in the preserved evidence."
    if any(not row.get("compile_passed") for row in undetected):
        return "mutation_fixture_defect", "The undetected mutation did not compile, so the qualification case itself is defective."
    if any(row.get("timeout_reported") for row in undetected):
        return "staging_or_identity_defect", "The undetected mutation reported a timeout, so identity or runtime state must be investigated separately."
    return (
        "inconclusive",
        "The mutation compiled and produced zero mismatches; without independent semantic analysis of the mutation and public stimulus, the preserved evidence cannot distinguish equivalence from insufficient stimulus.",
    )


def analyze_qualification_failures(
    *,
    run_root: Path,
    output_path: Path,
) -> tuple[dict[str, Any], int]:
    """Create one append-only, metadata-only failure analysis report."""

    try:
        report_path = run_root / "reports" / "asset_qualification_validation.json"
        evidence_path = run_root / "reports" / "asset_qualification_evidence.jsonl"
        sidecar_path = run_root / "qualification" / "attempt_01" / "staged" / "candidate_evidence.jsonl.runner.json"
        case_manifest_path = run_root / "qualification" / "attempt_01" / "case_manifest.jsonl"
        qualified_path = run_root / "qualification" / "attempt_01" / "qualified_task_ids.txt"
        failed_path = run_root / "qualification" / "attempt_01" / "failed_or_inconclusive_task_ids.txt"
        report = _load_json(report_path)
        evidence = _load_jsonl(evidence_path)
        cases = _load_jsonl(case_manifest_path)
        qualified = _read_ids(qualified_path)
        failed = _read_ids(failed_path)
        if report.get("run_id") != run_root.name or report.get("selected_tasks") != 20:
            raise QualificationAnalysisError("qualification report identity is invalid")
        if len(evidence) != 60 or len(cases) != 60:
            raise QualificationAnalysisError("qualification evidence/case count is not 60")
        report_by_source = {
            row.get("source_id"): row
            for row in report.get("rows", [])
            if isinstance(row, dict)
        }
        expected_failed_task_ids = [report_by_source[source_id].get("task_id") for source_id in EXPECTED_FAILED_SOURCES]
        if failed != expected_failed_task_ids:
            raise QualificationAnalysisError("failed task list is not the pinned excluded order")
        expected_all_task_ids = {row.get("task_id") for row in report.get("rows", [])}
        if len(set(qualified)) != 16 or set(qualified) & set(failed) or set(qualified) | set(failed) != expected_all_task_ids:
            raise QualificationAnalysisError("qualified and failed task lists overlap or have the wrong size")

        case_by_id = {row.get("qualification_case_id"): row for row in cases}
        evidence_by_id = {row.get("qualification_case_id"): row for row in evidence}
        if len(case_by_id) != 60 or len(evidence_by_id) != 60:
            raise QualificationAnalysisError("qualification rows contain duplicate case IDs")

        rows: list[dict[str, Any]] = []
        for source_id in EXPECTED_FAILED_SOURCES:
            source_cases = [row for row in cases if row.get("source_id") == source_id]
            source_evidence = [row for row in evidence if row.get("source_id") == source_id]
            if len(source_cases) != 3 or len(source_evidence) != 3:
                raise QualificationAnalysisError(f"failed source does not have three cases: {source_id}")
            task_ids = {row.get("task_id") for row in source_cases}
            if len(task_ids) != 1 or None in task_ids:
                raise QualificationAnalysisError(f"failed source task identity is ambiguous: {source_id}")
            for case in source_cases:
                evidence_row = evidence_by_id.get(case.get("qualification_case_id"))
                if evidence_row is None:
                    raise QualificationAnalysisError(f"missing evidence for case: {case.get('qualification_case_id')}")
                for key in ("candidate_id", "candidate_sha256", "testbench_sha256", "source_id", "task_id"):
                    if case.get(key) != evidence_row.get(key):
                        raise QualificationAnalysisError(f"case/evidence identity mismatch: {case.get('qualification_case_id')}")
            if source_id == "Prob115_shift18":
                classification, reason = _classify_mutation(source_evidence)
            else:
                classification, reason = _classify_positive(source_cases, source_evidence)
            allowed = MUTATION_CLASSIFICATIONS if source_id == "Prob115_shift18" else POSITIVE_CLASSIFICATIONS
            if classification not in allowed:
                raise QualificationAnalysisError(f"invalid classification for {source_id}")
            positive = next(row for row in source_evidence if row.get("kind") == "positive")
            rows.append({
                "source_id": source_id,
                "task_id": next(iter(task_ids)),
                "qualification_status": report_by_source[source_id].get("qualification_status"),
                "classification": classification,
                "reason": reason,
                "positive_case": _case_summary(
                    next(row for row in source_cases if row.get("kind") == "positive"),
                    positive,
                ),
                "negative_cases": [
                    _case_summary(case, evidence_by_id[case.get("qualification_case_id")])
                    for case in source_cases if case.get("kind") == "negative"
                ],
            })
        output = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_root.name,
            "parent_attempt": "qualification/attempt_01",
            "analysis_mode": "read_only_metadata_and_hashes",
            "generic_manual_run_validator_applicable": False,
            "qualification_validator_authoritative": True,
            "source_commit": report.get("source_commit"),
            "source_tree_sha256": report.get("source_tree_sha256"),
            "selection_ids_sha256": report.get("selection_ids_sha256"),
            "correction_manifest_sha256": report.get("correction_manifest_sha256"),
            "qualification_report_sha256": sha256_file(report_path),
            "qualification_evidence_sha256": sha256_file(evidence_path),
            "qualification_sidecar_sha256": sha256_file(sidecar_path) if sidecar_path.is_file() else report.get("qualification_sidecar_sha256"),
            "reference_inspected": False,
            "rerun": False,
            "raw_hdl_included": False,
            "rows": rows,
            "summary": {
                "failed_task_count": len(rows),
                "inconclusive_count": sum(row["classification"] == "inconclusive" for row in rows),
                "qualified_task_count_preserved": len(qualified),
                "retry_authorized": False,
            },
            "errors": [],
        }
        serialized = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        lowered = serialized.casefold()
        if any(marker.casefold() in lowered for marker in _FORBIDDEN_SERIALIZED_MARKERS):
            raise QualificationAnalysisError("analysis report contains a forbidden private marker")
        if output_path.exists() or output_path.is_symlink():
            raise QualificationAnalysisError("refusing to replace existing failure analysis")
        output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with output_path.open("xb") as handle:
            handle.write(serialized.encode("utf-8"))
        os.chmod(output_path, 0o600)
        return {"ok": True, "report": output}, 0
    except (OSError, UnicodeError, KeyError, TypeError, QualificationAnalysisError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1


__all__ = ["SCHEMA_VERSION", "QualificationAnalysisError", "analyze_qualification_failures"]
