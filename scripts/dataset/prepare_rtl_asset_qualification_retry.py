#!/usr/bin/env python3
"""Analyze a failed mutation qualification and prepare one contract-only retry.

This tool is intentionally narrow.  It reads the preserved v002 qualification
outputs, emits a sanitized forensic report, and creates a new retry input
snapshot whose only HDL change is the machine-readable mismatch label.  It
never edits the original correction overlay, qualification evidence, task
manifest, or verification-asset manifest.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any


SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
CORRECTION_VERSION = "assetfix_v002"
EXPECTED_SOURCE_IDS = (
    "Prob001_zero",
    "Prob020_mt2015_eq2",
    "Prob071_always_casez",
    "Prob048_m2014_q4c",
    "Prob079_fsm3onehot",
)

LEGACY_LABEL_RE = re.compile(r"(?i)mismatch_count_v1[ \t]*:")
CANONICAL_FORMAT_RE = re.compile(r"(?i)Mismatches[ \t]*:[ \t]*%[0-9]*d\b")
OBSERVED_COUNT_RE = re.compile(r"(?i)mismatch_count_v1[ \t]*:[ \t]*(\d+)")
RETURNCODE_RE = re.compile(r"(?i)returncode[ =](\d+)")
FINISH_RE = re.compile(r"(?i)\$finish\b")
TIMEOUT_RE = re.compile(r"(?i)timeout")
STARTUP_RE = re.compile(r"(?i)startup")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON input is not a regular file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSONL input is not a regular file: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} is not an object")
        rows.append(value)
    return rows


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing retry output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _write_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    _write_exclusive(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"), mode=mode)


def _write_jsonl(path: Path, rows: list[dict[str, Any]], *, mode: int = 0o600) -> None:
    _write_exclusive(path, b"".join(_canonical_json(row) for row in rows), mode=mode)


def _require_regular(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"input is not a regular non-link file: {path.name}")


def _copy_file(source: Path, destination: Path, *, mode: int) -> None:
    _require_regular(source)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"refusing to replace retry output: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, destination)
    os.chmod(destination, mode)


def _marker_summary(text: str) -> dict[str, Any]:
    return {
        "canonical_format_present": bool(CANONICAL_FORMAT_RE.search(text)),
        "canonical_format_count": len(CANONICAL_FORMAT_RE.findall(text)),
        "legacy_label_present": bool(LEGACY_LABEL_RE.search(text)),
        "legacy_label_count": len(LEGACY_LABEL_RE.findall(text)),
        "display_count": len(re.findall(r"(?i)\$display\b", text)),
        "finish_count": len(FINISH_RE.findall(text)),
        "fatal_count": len(re.findall(r"(?i)\$fatal\b", text)),
        "early_stop_count": len(re.findall(r"(?i)\$stop\b", text)),
        "testbench_top_declared": bool(re.search(r"(?i)\bmodule\s+tb\b", text)),
    }


def _diagnostic_summary(values: Any) -> dict[str, Any]:
    diagnostics = values if isinstance(values, list) else []
    counts = [int(match) for value in diagnostics if isinstance(value, str) for match in OBSERVED_COUNT_RE.findall(value)]
    return {
        "diagnostic_count": len(diagnostics),
        "workspace_redaction_present": any("<workspace>" in value for value in diagnostics if isinstance(value, str)),
        "observed_mismatch_counts": counts,
        "observed_maximum_mismatch_count": max(counts) if counts else None,
        "simulation_returncodes": sorted({int(match) for value in diagnostics if isinstance(value, str) for match in RETURNCODE_RE.findall(value)}),
        "finish_marker_present": any(FINISH_RE.search(value) for value in diagnostics if isinstance(value, str)),
        "timeout_marker_present": any(TIMEOUT_RE.search(value) for value in diagnostics if isinstance(value, str)),
        "startup_error_marker_present": any(STARTUP_RE.search(value) for value in diagnostics if isinstance(value, str)),
    }


def _status(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"attempted": None, "passed": None, "reason": "missing"}
    return {key: value.get(key) for key in ("attempted", "passed", "reason")}


def _forensic_report(
    *,
    run_root: Path,
    correction_root: Path,
    qualification_root: Path,
    known_good_testbench: Path,
    known_good_evidence: Path,
    output_path: Path,
) -> dict[str, Any]:
    manifest_rows = _load_jsonl(qualification_root / "mutation_manifest.jsonl")
    evidence_rows = _load_jsonl(qualification_root / "mutation_evidence.jsonl")
    if len(manifest_rows) != 10 or len(evidence_rows) != 10:
        raise ValueError("forensic analysis requires exactly ten preserved mutation rows")

    manifest_by_id = {row["mutation_id"]: row for row in manifest_rows}
    evidence_by_id = {row["mutation_id"]: row for row in evidence_rows}
    if set(manifest_by_id) != set(evidence_by_id):
        raise ValueError("preserved mutation manifest and evidence IDs differ")

    rows: list[dict[str, Any]] = []
    all_compile_passed = True
    all_simulation_attempted = True
    timeout_count = 0
    startup_failure_count = 0
    recognized_positive_count = 0
    for mutation_id in (row["mutation_id"] for row in manifest_rows):
        manifest = manifest_by_id[mutation_id]
        evidence = evidence_by_id[mutation_id]
        checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
        compile_checks = checks.get("compile") if isinstance(checks.get("compile"), dict) else {}
        simulation_checks = checks.get("simulation") if isinstance(checks.get("simulation"), dict) else {}
        diagnostics = _diagnostic_summary(evidence.get("diagnostics"))
        all_compile_passed = all_compile_passed and all(
            _status(compile_checks.get(name))["passed"] is True for name in ("original", "mutated", "repaired")
        )
        all_simulation_attempted = all_simulation_attempted and all(
            _status(simulation_checks.get(name))["attempted"] is True for name in ("original_passes", "mutated_detects_mutation", "repaired_passes")
        )
        timeout_count += int(diagnostics["timeout_marker_present"])
        startup_failure_count += int(diagnostics["startup_error_marker_present"])
        recognized_positive_count += int(
            _status(simulation_checks.get("mutated_detects_mutation"))["passed"] is True
        )
        rows.append({
            "mutation_id": mutation_id,
            "source_id": manifest.get("source_id"),
            "candidate_id": None,
            "evidence_kind": "rtl_mutation_evidence_v0.1",
            "top_module": manifest.get("top_module"),
            "testbench_top": "tb",
            "requested_checks": manifest.get("requested_checks"),
            "manifest_result_pattern_field_present": "simulation_result_contract" in manifest,
            "expected_result_pattern": "Mismatches: <non-negative decimal integer>",
            "compile": {
                name: _status(compile_checks.get(name)) for name in ("original", "mutated", "repaired")
            },
            "simulation": {
                name: _status(simulation_checks.get(name)) for name in ("original_passes", "mutated_detects_mutation", "repaired_passes")
            },
            "failure_category": evidence.get("failure_category"),
            "evidence_tier": evidence.get("evidence_tier"),
            "diagnostics": diagnostics,
        })

    old_testbench_summaries = []
    for source_id in EXPECTED_SOURCE_IDS:
        path = qualification_root / "workspace" / source_id / "testbench.sv"
        text = path.read_text(encoding="utf-8")
        old_testbench_summaries.append({"source_id": source_id, **_marker_summary(text), "sha256": _sha256_file(path)})

    known_good_text = known_good_testbench.read_text(encoding="utf-8")
    known_good_evidence_rows = _load_jsonl(known_good_evidence)
    if len(known_good_evidence_rows) != 1:
        raise ValueError("known-good pilot evidence must contain one row")
    known_good_row = known_good_evidence_rows[0]

    report = {
        "schema_version": "rtl_asset_qualification_failure_analysis_v0.1",
        "run_id": run_root.name,
        "classification": "testbench_result_format_defect",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "correction_version": CORRECTION_VERSION,
        "execution_summary": {
            "mutation_row_count": len(evidence_rows),
            "compile_all_original_mutated_repaired_passed": all_compile_passed,
            "all_simulation_paths_attempted": all_simulation_attempted,
            "simulation_timeout_count": timeout_count,
            "simulation_startup_failure_count": startup_failure_count,
            "recognized_mutation_count": recognized_positive_count,
            "simulation_not_detected_count": sum(
                int(row["failure_category"] == "simulation_not_detected") for row in rows
            ),
        },
        "parser_contract": {
            "pinned_rtlbench_contract": "Mismatches: <non-negative decimal integer>",
            "v002_emitted_label": "mismatch_count_v1: <integer>",
            "recognized_result_is_complete_line": True,
            "mutation_manifest_declares_result_pattern": False,
        },
        "rows": rows,
        "format_comparison": {
            "v002_testbenches": old_testbench_summaries,
            "known_good_pilot": {
                **_marker_summary(known_good_text),
                "sha256": _sha256_file(known_good_testbench),
                "evidence_schema_version": known_good_row.get("schema_version"),
                "simulation_result_contract": known_good_row.get("simulation_result_contract"),
                "simulation_passed": (known_good_row.get("checks", {}).get("simulation", {}).get("candidate_passes", {}).get("passed") is True),
                "reported_counts": known_good_row.get("mismatch_summary", {}).get("reported_counts"),
            },
        },
        "root_cause": {
            "simulation_started": True,
            "simulation_reached_finish": True,
            "positive_counts_observed_in_sanitized_diagnostics": True,
            "positive_counts_recognized_by_mutation_parser": False,
            "qualification_validator_defect": False,
            "mutation_manifest_defect": False,
            "behavioral_weakness_proven": False,
        },
        "preserved_artifacts": {
            "qualification_manifest_sha256": _sha256_file(qualification_root / "mutation_manifest.jsonl"),
            "qualification_evidence_sha256": _sha256_file(qualification_root / "mutation_evidence.jsonl"),
            "qualification_sidecar_sha256": _sha256_file(qualification_root / "mutation_evidence.runner.json"),
            "assetfix_v002_manifest_sha256": _sha256_file(correction_root / "manifest.jsonl"),
        },
        "decision": {
            "assetfix_v003_created": False,
            "teacher_generation_allowed": False,
            "retry_contract_only_overlay_allowed": True,
            "original_v002_inputs_modified": False,
            "raw_private_hdl_in_report": False,
        },
    }
    _write_json(output_path, report)
    return report


def _prepare_retry_overlay(
    *,
    run_root: Path,
    correction_root: Path,
    qualification_root: Path,
    correction_report_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists() or output_root.is_symlink():
        raise ValueError(f"retry output already exists: {output_root.name}")
    output_root.mkdir(mode=0o700, parents=False)

    base_manifest = _load_jsonl(correction_root / "manifest.jsonl")
    base_tasks = _load_jsonl(run_root / "tasks" / "generation_tasks.jsonl")
    base_assets = _load_jsonl(run_root / "tasks" / "verification_assets.jsonl")
    mutation_manifest = _load_jsonl(qualification_root / "mutation_manifest.jsonl")
    correction_report = _load_json(correction_report_path)
    if [row.get("source_id") for row in base_assets] != list(EXPECTED_SOURCE_IDS):
        raise ValueError("base verification assets are not the expected five rows")
    if [row.get("source_id") for row in base_tasks] != list(EXPECTED_SOURCE_IDS):
        raise ValueError("base generation tasks are not the expected five rows")

    correction_by_id = {row["source_id"]: row for row in base_manifest}
    asset_by_id = {row["source_id"]: row for row in base_assets}
    if set(correction_by_id) != set(EXPECTED_SOURCE_IDS):
        raise ValueError("base correction manifest IDs do not match the expected five rows")

    correction_output = output_root / "correction"
    input_output = output_root / "input"
    task_output = output_root / "tasks"
    runner_output = output_root / "runner-output"
    for directory in (correction_output, input_output, task_output, runner_output):
        directory.mkdir(mode=0o700)
    input_output.chmod(0o755)
    (correction_output / "tasks").mkdir(mode=0o700)
    (input_output / "workspace").mkdir(mode=0o755)
    runner_output.chmod(0o777)

    updated_hashes: dict[str, str] = {}
    updated_corrections: list[dict[str, Any]] = []
    updated_assets = copy.deepcopy(base_assets)
    base_workspace = qualification_root / "workspace"
    retry_workspace = input_output / "workspace"

    for source_id in EXPECTED_SOURCE_IDS:
        base_tb = correction_root / "tasks" / source_id / "testbench.sv"
        base_qualification_tb = base_workspace / source_id / "testbench.sv"
        if base_tb.read_bytes() != base_qualification_tb.read_bytes():
            raise ValueError(f"base correction and qualification testbench differ: {source_id}")
        original_text = base_tb.read_text(encoding="utf-8")
        legacy_matches = LEGACY_LABEL_RE.findall(original_text)
        if len(legacy_matches) != 1 or CANONICAL_FORMAT_RE.search(original_text):
            raise ValueError(f"base testbench is not the expected legacy-only contract: {source_id}")
        repaired_text = LEGACY_LABEL_RE.sub("Mismatches:", original_text, count=1)
        if LEGACY_LABEL_RE.search(repaired_text) or len(CANONICAL_FORMAT_RE.findall(repaired_text)) != 1:
            raise ValueError(f"contract-only repair did not produce one canonical result: {source_id}")
        if LEGACY_LABEL_RE.sub("Mismatches:", original_text, count=1) != repaired_text:
            raise AssertionError("contract replacement was not deterministic")
        repaired_bytes = repaired_text.encode("utf-8")
        updated_hashes[source_id] = _sha256_bytes(repaired_bytes)

        correction_destination = correction_output / "tasks" / source_id / "testbench.sv"
        _write_exclusive(correction_destination, repaired_bytes, mode=0o600)

        input_source_dir = base_workspace / source_id
        input_destination_dir = retry_workspace / source_id
        input_destination_dir.mkdir(mode=0o755)
        for filename in ("original.sv", "repaired.sv"):
            _copy_file(input_source_dir / filename, input_destination_dir / filename, mode=0o644)
        for mutation_path in sorted(input_source_dir.glob("*.sv")):
            if mutation_path.name in {"original.sv", "repaired.sv", "testbench.sv"}:
                continue
            _copy_file(mutation_path, input_destination_dir / mutation_path.name, mode=0o644)
        _write_exclusive(input_destination_dir / "testbench.sv", repaired_bytes, mode=0o644)

        correction_row = copy.deepcopy(correction_by_id[source_id])
        correction_row["corrected_testbench_sha256"] = updated_hashes[source_id]
        updated_corrections.append(correction_row)
        asset = next(row for row in updated_assets if row["source_id"] == source_id)
        asset["input_hashes"]["testbench_sha256"] = updated_hashes[source_id]

    # Only the corrected private testbench hash changes in the retry asset view.
    _write_jsonl(correction_output / "manifest.jsonl", updated_corrections, mode=0o600)
    _write_jsonl(task_output / "generation_tasks.jsonl", base_tasks, mode=0o600)
    _write_jsonl(task_output / "verification_assets.jsonl", updated_assets, mode=0o600)
    _write_jsonl(input_output / "mutation_manifest.jsonl", mutation_manifest, mode=0o644)

    retry_report = {
        "schema_version": "rtl_verification_asset_contract_retry_v0.1",
        "source_dataset": correction_report.get("source_dataset", "VerilogEval"),
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "base_split_sha256": FROZEN_SPLIT_SHA256,
        "correction_version": CORRECTION_VERSION,
        "contract_repair_kind": "result_label_only",
        "base_correction_manifest_sha256": _sha256_file(correction_root / "manifest.jsonl"),
        "correction_manifest_sha256": _sha256_file(correction_output / "manifest.jsonl"),
        "base_correction_report_sha256": _sha256_file(correction_report_path),
        "reference_modified": False,
        "reference_copied_to_support": False,
        "dependency_closure_passed": True,
        "verification_readiness": "executable_ready",
        "negative_mutation_execution": "retry_01",
        "rows": [
            {"source_id": source_id, "corrected_testbench_sha256": updated_hashes[source_id]}
            for source_id in EXPECTED_SOURCE_IDS
        ],
        "errors": [],
    }
    _write_json(output_root / "correction_report.json", retry_report)

    return {
        "output_root": output_root.name,
        "source_ids": list(EXPECTED_SOURCE_IDS),
        "mutation_manifest_rows": len(mutation_manifest),
        "task_rows": len(base_tasks),
        "asset_rows": len(updated_assets),
        "base_correction_manifest_sha256": retry_report["base_correction_manifest_sha256"],
        "retry_correction_manifest_sha256": retry_report["correction_manifest_sha256"],
        "testbench_sha256": updated_hashes,
        "reference_copied": False,
        "support_files": [],
        "teacher_generation_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--correction-report", required=True, type=Path)
    parser.add_argument("--qualification-root", required=True, type=Path)
    parser.add_argument("--known-good-testbench", required=True, type=Path)
    parser.add_argument("--known-good-evidence", required=True, type=Path)
    parser.add_argument("--failure-report", required=True, type=Path)
    parser.add_argument("--retry-root", required=True, type=Path)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="reuse an already-created forensic report without rewriting it",
    )
    args = parser.parse_args(argv)
    try:
        if args.prepare_only:
            report = _load_json(args.failure_report)
            if not isinstance(report, dict) or report.get("classification") != "testbench_result_format_defect":
                raise ValueError("existing forensic report is not the expected format-defect report")
        else:
            report = _forensic_report(
                run_root=args.run_root,
                correction_root=args.correction_root,
                qualification_root=args.qualification_root,
                known_good_testbench=args.known_good_testbench,
                known_good_evidence=args.known_good_evidence,
                output_path=args.failure_report,
            )
        retry = _prepare_retry_overlay(
            run_root=args.run_root,
            correction_root=args.correction_root,
            qualification_root=args.qualification_root,
            correction_report_path=args.correction_report,
            output_root=args.retry_root,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({
        "ok": True,
        "classification": report["classification"],
        "failure_report": args.failure_report.name,
        "retry": retry,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
