from __future__ import annotations

import hashlib
import json

from scripts.dataset.rtl_generation_dataset import (
    GENERATION_SFT_SCHEMA_VERSION,
    SMOKE_SOURCE_IDS,
    SMOKE_TASK_IDS,
    _validate_attempt_row,
    _contains_private_marker,
    _package_task_records,
    _validate_qualified_subset_provenance,
    _validate_smoke_task_order,
    _make_row,
    package_verified_rtl_generation_dataset,
    validate_generation_sft_row,
    validate_generation_sft_package,
)
from scripts.dataset.rtl_generation_preparation import GENERATION_TASK_SCHEMA_VERSION


COMMIT = "b" * 40


def _task() -> dict:
    return {
        "schema_version": GENERATION_TASK_SCHEMA_VERSION,
        "task_id": "rtlgen_synthetic_task_001",
        "source_id": "Prob001",
        "source_dataset": "VerilogEval",
        "design_family": "combinational",
        "language": "systemverilog",
        "specification": "Implement TopModule with output zero tied low.",
        "top_module": "TopModule",
        "interface": {"ports": [{"name": "zero", "direction": "output", "declaration": "output zero", "packed_range": None, "width_bits": 1, "signed": False, "description": None}]},
        "clocking": {"clock_signal": None, "edge": None},
        "reset": {"signal": None, "active_level": None, "synchronous": None},
        "latency_contract": None,
        "behavioral_constraints": [],
        "assumptions": [],
        "ambiguities": [],
        "provenance": {
            "public_dataset_name": "VerilogEval",
            "public_dataset_url": "https://example.invalid/verilog-eval",
            "source_commit": COMMIT,
            "license": "MIT",
            "original_source_id": "Prob001",
        },
    }


def _candidate(task: dict) -> tuple[dict, str]:
    rtl = "module TopModule(output logic zero); assign zero = 1'b0; endmodule\n"
    candidate = {
        "schema_version": "rtl_teacher_candidate_v0.1",
        "task_id": task["task_id"],
        "top_module": "TopModule",
        "language": "systemverilog",
        "rtl": rtl,
        "implementation_summary": "Continuously drives the output low.",
        "assumptions": [],
    }
    return candidate, hashlib.sha256(rtl.encode()).hexdigest()


def _checks() -> dict:
    return {
        "compile": {"candidate": {"attempted": True, "passed": True, "reason": None}},
        "simulation": {"candidate_passes": {"attempted": True, "passed": True, "reason": None}},
        "lint": {"candidate": {"attempted": False, "passed": None, "reason": "not_requested"}},
        "synthesis": {"candidate": {"attempted": False, "passed": None, "reason": "not_requested"}},
    }


def _attempt(candidate_id: str, task: dict, candidate_hash: str) -> dict:
    return {
        "schema_version": "rtl_generation_attempt_v0.1",
        "candidate_id": candidate_id,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 1,
        "top_module": "TopModule",
        "candidate_sha256": candidate_hash,
        "verification_profile": "verilog_eval_mismatch_v1",
        "accepted": True,
        "failure_category": "passed",
        "checks": _checks(),
        "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [0], "reported_sample_counts": [1], "maximum_count": 0, "timeout_reported": False},
        "diagnostics": [],
        "toolchain": {
            "iverilog": {"available": True, "version": "synthetic"},
            "vvp": {"available": True, "version": "synthetic"},
            "verilator": {"available": False, "version": None},
            "yosys": {"available": False, "version": None},
        },
    }


def _evidence(attempt: dict, candidate_hash: str) -> dict:
    return {
        "schema_version": "rtl_candidate_evidence_v0.1",
        "candidate_id": attempt["candidate_id"],
        "task_id": attempt["task_id"],
        "source_id": attempt["source_id"],
        "attempt": 1,
        "top_module": "TopModule",
        "testbench_top": "tb",
        "simulation_result_contract": "mismatch_count_v1",
        "requested_checks": {"compile": True, "simulation": True, "lint": False, "synthesis": False},
        "input_hashes": {"candidate_rtl_sha256": candidate_hash, "testbench_sha256": "c" * 64, "support_files": []},
        "toolchain": attempt["toolchain"],
        "checks": attempt["checks"],
        "mismatch_summary": attempt["mismatch_summary"],
        "failure_category": "passed",
        "accepted": True,
        "diagnostics": [],
    }


def _split(task: dict, tasks_path) -> dict:
    return {
        "schema_version": "rtl_generation_split_v0.1",
        "inventory_sha256": "1" * 64,
        "inventory_path": "inventory.jsonl",
        "algorithm": "family_isolated_random_v1",
        "seed": 7,
        "ratios": {"train": 0.7, "validation": 0.15, "test": 0.15},
        "row_count": 1,
        "eligible_generation_policy": {"split": "train", "verification_readiness": "executable_ready"},
        "splits": {"train": [task["source_id"]], "validation": [], "test": []},
        "rows": [{"source_id": task["source_id"], "task_id": task["task_id"], "design_family": task["design_family"], "verification_readiness": "executable_ready", "split": "train"}],
        "counts": {"train": 1, "validation": 0, "test": 0},
        "errors": [],
    }


def _write_verified_fixture(tmp_path):
    task = _task()
    candidate, candidate_hash = _candidate(task)
    candidate_id = f"{task['task_id']}_attempt_01"
    record = {
        "schema_version": "rtl_teacher_candidate_record_v0.1",
        "candidate_id": candidate_id,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 1,
        "packet_id": "packet",
        "candidate_sha256": candidate_hash,
        "candidate": candidate,
    }
    attempt = _attempt(candidate_id, task, candidate_hash)
    evidence = _evidence(attempt, candidate_hash)
    tasks_path = tmp_path / "tasks.jsonl"
    candidates_path = tmp_path / "candidates.jsonl"
    attempts_path = tmp_path / "attempts.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    split_path = tmp_path / "split.json"
    tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
    candidates_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    attempts_path.write_text(json.dumps(attempt) + "\n", encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
    split_path.write_text(json.dumps(_split(task, tasks_path)) + "\n", encoding="utf-8")
    sidecar = tmp_path / "evidence.jsonl.runner.json"
    sidecar.write_text(json.dumps({
        "schema_version": "rtlbench_runner_identity_v0.2",
        "profile": "pilot-docker",
        "runtime": "docker",
        "runtime_mode": "rootful-daemon",
        "rootless": False,
        "image_identity_kind": "local-image-id",
        "image": "sha256:" + "d" * 64,
        "image_id": "sha256:" + "d" * 64,
        "image_digest": None,
        "rtlbench_commit": COMMIT,
        "runner_config_version": "rtlbench_rootless_runner_v0.1",
        "manifest_sha256": "e" * 64,
        "workspace_tree_sha256": "f" * 64,
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "partial_evidence_sha256": None,
    }) + "\n", encoding="utf-8")
    return {
        "task": task,
        "tasks": tasks_path,
        "candidates": candidates_path,
        "attempts": attempts_path,
        "evidence": evidence_path,
        "split": split_path,
        "sidecar": sidecar,
    }


def _write_five_verified_fixture(tmp_path):
    tasks = []
    records = []
    attempts = []
    evidence = []
    for index, (source_id, task_id) in enumerate(zip(SMOKE_SOURCE_IDS, SMOKE_TASK_IDS)):
        task = _task()
        task["source_id"] = source_id
        task["task_id"] = task_id
        task["provenance"]["original_source_id"] = source_id
        candidate, candidate_hash = _candidate(task)
        candidate_id = f"{task_id}_attempt_01"
        records.append({
            "schema_version": "rtl_teacher_candidate_record_v0.1",
            "candidate_id": candidate_id,
            "task_id": task_id,
            "source_id": source_id,
            "attempt": 1,
            "packet_id": f"packet_{index:04d}",
            "candidate_sha256": candidate_hash,
            "candidate": candidate,
        })
        attempt = _attempt(candidate_id, task, candidate_hash)
        attempts.append(attempt)
        evidence.append(_evidence(attempt, candidate_hash))
        tasks.append(task)

    tasks_path = tmp_path / "five-tasks.jsonl"
    candidates_path = tmp_path / "five-candidates.jsonl"
    attempts_path = tmp_path / "five-attempts.jsonl"
    evidence_path = tmp_path / "five-evidence.jsonl"
    split_path = tmp_path / "five-split.json"
    tasks_path.write_text("".join(json.dumps(row) + "\n" for row in tasks), encoding="utf-8")
    candidates_path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    attempts_path.write_text("".join(json.dumps(row) + "\n" for row in attempts), encoding="utf-8")
    evidence_path.write_text("".join(json.dumps(row) + "\n" for row in evidence), encoding="utf-8")
    split_path.write_text(json.dumps({
        "schema_version": "rtl_generation_split_v0.1",
        "inventory_sha256": "1" * 64,
        "inventory_path": "inventory.jsonl",
        "algorithm": "family_isolated_random_v1",
        "seed": 7,
        "ratios": {"train": 0.7, "validation": 0.15, "test": 0.15},
        "row_count": 5,
        "eligible_generation_policy": {"split": "train", "verification_readiness": "executable_ready"},
        "splits": {"train": [row["source_id"] for row in tasks], "validation": [], "test": []},
        "rows": [
            {
                "source_id": row["source_id"],
                "task_id": row["task_id"],
                "design_family": row["design_family"],
                "verification_readiness": "executable_ready",
                "split": "train",
            }
            for row in tasks
        ],
        "counts": {"train": 5, "validation": 0, "test": 0},
        "errors": [],
    }) + "\n", encoding="utf-8")
    sidecar = tmp_path / "five-evidence.jsonl.runner.json"
    sidecar.write_text(json.dumps({
        "schema_version": "rtlbench_runner_identity_v0.2",
        "profile": "pilot-docker",
        "runtime": "docker",
        "runtime_mode": "rootful-daemon",
        "rootless": False,
        "image_identity_kind": "local-image-id",
        "image": "sha256:" + "d" * 64,
        "image_id": "sha256:" + "d" * 64,
        "image_digest": None,
        "rtlbench_commit": COMMIT,
        "runner_config_version": "rtlbench_rootless_runner_v0.1",
        "manifest_sha256": "e" * 64,
        "workspace_tree_sha256": "f" * 64,
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "partial_evidence_sha256": None,
    }) + "\n", encoding="utf-8")
    return {
        "tasks": tasks_path,
        "candidates": candidates_path,
        "attempts": attempts_path,
        "evidence": evidence_path,
        "split": split_path,
        "sidecar": sidecar,
    }


def test_package_accepts_verified_candidate_and_validates_output(tmp_path) -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    candidate_id = f"{task['task_id']}_attempt_01"
    record = {
        "schema_version": "rtl_teacher_candidate_record_v0.1",
        "candidate_id": candidate_id,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 1,
        "packet_id": "rtl_teacher_initial_batch_0001_aaaaaaaaaaaa",
        "candidate_sha256": candidate_hash,
        "candidate": candidate,
    }
    attempt = _attempt(candidate_id, task, candidate_hash)
    evidence = _evidence(attempt, candidate_hash)
    tasks_path = tmp_path / "tasks.jsonl"
    candidates_path = tmp_path / "candidates.jsonl"
    attempts_path = tmp_path / "attempts.jsonl"
    evidence_path = tmp_path / "evidence.jsonl"
    split_path = tmp_path / "split.json"
    tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
    candidates_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    attempts_path.write_text(json.dumps(attempt) + "\n", encoding="utf-8")
    evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
    split_path.write_text(json.dumps(_split(task, tasks_path)) + "\n", encoding="utf-8")
    sidecar = tmp_path / "evidence.jsonl.runner.json"
    sidecar.write_text(json.dumps({
        "schema_version": "rtlbench_runner_identity_v0.2",
        "profile": "pilot-docker",
        "runtime": "docker",
        "runtime_mode": "rootful-daemon",
        "rootless": False,
        "image_identity_kind": "local-image-id",
        "image": "sha256:" + "d" * 64,
        "image_id": "sha256:" + "d" * 64,
        "image_digest": None,
        "rtlbench_commit": COMMIT,
        "runner_config_version": "rtlbench_rootless_runner_v0.1",
        "manifest_sha256": "e" * 64,
        "workspace_tree_sha256": "f" * 64,
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "partial_evidence_sha256": None,
    }) + "\n", encoding="utf-8")
    output = tmp_path / "package"
    result, code = package_verified_rtl_generation_dataset(
        tasks_path, candidates_path, attempts_path, split_path, output,
        evidence_path=evidence_path,
        sidecar_path=sidecar,
        expected_rtlbench_commit=COMMIT,
    )
    assert code == 0, result
    assert result["primary_rows"] == 1
    assert result["all_rows"] == 1
    assert (output / "all.jsonl").is_file()
    assert (output / "rejected_rows.jsonl").is_file()
    assert (output / "validation_report.json").is_file()
    assert (output / "validation_report.md").is_file()
    assert (output / "provenance_report.json").is_file()
    report, code = validate_generation_sft_package(output)
    assert code == 0, report
    row = json.loads((output / "train.jsonl").read_text(encoding="utf-8"))
    assert row["schema_version"] == GENERATION_SFT_SCHEMA_VERSION
    assert row["messages"][1]["content"] == task
    assert row["messages"][2]["content"] == candidate["rtl"]
    assert row["promotion_allowed"] is False


def test_accepted_sanitized_compile_warning_is_preserved_as_metadata() -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    attempt = _attempt(
        f"{task['task_id']}_attempt_01",
        task,
        candidate_hash,
    )
    attempt["diagnostics"] = [
        "compile: returncode=0 <source omitted>:5: warning: sanitized compiler warning"
    ]

    assert _validate_attempt_row(attempt, "attempt") == []


def test_accepted_raw_diagnostic_is_rejected() -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    attempt = _attempt(
        f"{task['task_id']}_attempt_01",
        task,
        candidate_hash,
    )
    attempt["diagnostics"] = ["compile: /home/private/source.sv:5: warning"]

    errors = _validate_attempt_row(attempt, "attempt")
    assert any("sanitized compile warning" in error for error in errors)


def test_package_task_records_read_user_content() -> None:
    tasks = [
        {"source_id": source_id, "task_id": task_id}
        for source_id, task_id in zip(SMOKE_SOURCE_IDS, SMOKE_TASK_IDS)
    ]
    rows = [
        {
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": task},
                {"role": "assistant", "content": "module TopModule; endmodule"},
            ]
        }
        for task in tasks
    ]

    assert _package_task_records(rows) == tasks


def test_private_scan_ignores_metadata_keys_but_checks_values() -> None:
    assert _contains_private_marker({"rtlbench_commit": "a" * 40}) is False
    assert _contains_private_marker({"diagnostic": "/tmp/private.sv"}) is True


def test_qualified_subset_provenance_accepts_passed_subset_binding(tmp_path) -> None:
    task = _task()
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps(_split(task, split_path)) + "\n", encoding="utf-8")

    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "schema_version": "rtl_asset_qualification_report_v0.1",
        "rows": [{
            "source_id": task["source_id"],
            "task_id": task["task_id"],
            "qualification_passed": True,
        }],
    }) + "\n", encoding="utf-8")
    correction = tmp_path / "correction.jsonl"
    correction.write_text(json.dumps({
        "source_id": task["source_id"],
        "task_id": task["task_id"],
        "split": "train",
        "correction_version": "assetfix_v004",
        "dependency_closure": "passed",
        "verification_readiness": "executable_ready",
        "support_files": [],
        "reference_copied_to_support": False,
        "corrected_testbench_sha256": "c" * 64,
    }) + "\n", encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({
        "schema_version": "rtl_generation_qualified_subset_binding_v0.1",
        "source_commit": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "frozen_split_sha256": hashlib.sha256(split_path.read_bytes()).hexdigest(),
        "correction_version": "assetfix_v004",
        "qualification_result": "passed",
        "qualification_validator_authoritative": True,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_report_sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
        "qualified_correction_manifest_sha256": hashlib.sha256(correction.read_bytes()).hexdigest(),
        "qualified_task_count": 1,
        "rows": [{
            "source_id": task["source_id"],
            "task_id": task["task_id"],
            "qualification_result": "passed",
            "corrected_testbench_sha256": "c" * 64,
        }],
    }) + "\n", encoding="utf-8")

    context, errors = _validate_qualified_subset_provenance(
        tasks=[task],
        split_path=split_path,
        base_split_path=None,
        source_acquisition_path=None,
        asset_qualification_path=qualification,
        qualification_binding_path=binding,
        qualified_correction_manifest_path=correction,
        expected_source_commit="a" * 40,
        expected_source_tree_sha256="b" * 64,
        expected_frozen_split_sha256=hashlib.sha256(split_path.read_bytes()).hexdigest(),
    )

    assert errors == []
    assert context["qualification_passed"] is True
    assert context["qualified_source_ids"] == [task["source_id"]]


def test_qualified_subset_provenance_rejects_unbound_task(tmp_path) -> None:
    task = _task()
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps(_split(task, split_path)) + "\n", encoding="utf-8")
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "schema_version": "rtl_asset_qualification_report_v0.1",
        "rows": [],
    }) + "\n", encoding="utf-8")
    correction = tmp_path / "correction.jsonl"
    correction.write_text(json.dumps({"source_id": "other", "task_id": "other"}) + "\n", encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({
        "schema_version": "rtl_generation_qualified_subset_binding_v0.1",
        "rows": [],
    }) + "\n", encoding="utf-8")

    _, errors = _validate_qualified_subset_provenance(
        tasks=[task],
        split_path=split_path,
        base_split_path=None,
        source_acquisition_path=None,
        asset_qualification_path=qualification,
        qualification_binding_path=binding,
        qualified_correction_manifest_path=correction,
        expected_source_commit="a" * 40,
        expected_source_tree_sha256="b" * 64,
        expected_frozen_split_sha256=hashlib.sha256(split_path.read_bytes()).hexdigest(),
    )

    assert any("absent from qualified subset" in error for error in errors)


def test_qualified_subset_provenance_accepts_combined_binding_report(tmp_path) -> None:
    task = _task()
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps(_split(task, split_path)) + "\n", encoding="utf-8")
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({
        "schema_version": "rtl_generation_combined_qualification_binding_v0.1",
        "qualification_passed": True,
        "selected_task_count": 1,
        "rows": [{
            "source_id": task["source_id"],
            "task_id": task["task_id"],
            "qualification_result": "passed",
        }],
    }) + "\n", encoding="utf-8")
    correction = tmp_path / "correction.jsonl"
    correction.write_text(json.dumps({
        "source_id": task["source_id"],
        "task_id": task["task_id"],
        "split": "train",
        "correction_version": "assetfix_v004",
        "dependency_closure": "passed",
        "verification_readiness": "executable_ready",
        "support_files": [],
        "reference_copied_to_support": False,
        "corrected_testbench_sha256": "c" * 64,
    }) + "\n", encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({
        "schema_version": "rtl_generation_qualified_subset_binding_v0.1",
        "source_commit": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "frozen_split_sha256": hashlib.sha256(split_path.read_bytes()).hexdigest(),
        "correction_version": "assetfix_v004",
        "qualification_result": "passed",
        "qualification_validator_authoritative": True,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_report_sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
        "qualified_correction_manifest_sha256": hashlib.sha256(correction.read_bytes()).hexdigest(),
        "qualified_task_count": 1,
        "rows": [{
            "source_id": task["source_id"],
            "task_id": task["task_id"],
            "qualification_result": "passed",
            "corrected_testbench_sha256": "c" * 64,
        }],
    }) + "\n", encoding="utf-8")

    context, errors = _validate_qualified_subset_provenance(
        tasks=[task],
        split_path=split_path,
        base_split_path=None,
        source_acquisition_path=None,
        asset_qualification_path=qualification,
        qualification_binding_path=binding,
        qualified_correction_manifest_path=correction,
        expected_source_commit="a" * 40,
        expected_source_tree_sha256="b" * 64,
        expected_frozen_split_sha256=hashlib.sha256(split_path.read_bytes()).hexdigest(),
    )

    assert errors == []
    assert context["qualification_passed"] is True


def test_repair_lineage_is_validated_on_new_rows() -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    candidate_record = {
        "candidate_id": f"{task['task_id']}_attempt_02",
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 2,
        "candidate_sha256": candidate_hash,
        "candidate": candidate,
    }
    attempt = _attempt(candidate_record["candidate_id"], task, candidate_hash)
    attempt["attempt"] = 2
    row = _make_row(
        task,
        candidate_record,
        attempt,
        None,
        None,
        "train",
        {},
        repair_lineage={
            "accepted_attempt": 2,
            "prior_failed_attempts": 1,
            "repair_used": True,
            "failure_category_before_repair": "compile_failure",
        },
    )
    assert validate_generation_sft_row(row) == []


def test_package_privacy_scope_covers_metadata_paths_testbench_mutations_and_reference() -> None:
    assert _contains_private_marker({"verification": {"rtlbench_commit": "RTLBench commit fcad47e"}}) is False
    assert _contains_private_marker({"messages": [{"role": "user", "content": "module tb; endmodule"}]}) is True
    assert _contains_private_marker({"metadata": {"path": "/home/private/testbench.sv"}}) is True
    assert _contains_private_marker({"diagnostics": [["mutation_id=mut_01"]]}) is True
    assert _contains_private_marker({"messages": [{"role": "assistant", "content": "module RefModule; endmodule"}]}) is True


def test_pinned_smoke_order_rejects_reordered_tasks() -> None:
    ordered = [
        {"source_id": source_id, "task_id": task_id}
        for source_id, task_id in zip(SMOKE_SOURCE_IDS, SMOKE_TASK_IDS)
    ]
    reordered = [ordered[1], ordered[0], *ordered[2:]]

    assert _validate_smoke_task_order(ordered) == []
    assert _validate_smoke_task_order(reordered) == [
        "tasks do not match the pinned five-row smoke order"
    ]


def test_recovery_package_lineage_requires_authorization_and_consumable_state(tmp_path) -> None:
    fixture = _write_verified_fixture(tmp_path)
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({
        "schema_version": "rtl_dataset_packaging_recovery_authorization_v0.1",
        "authorization_status": "one_recovery_invocation_authorized",
        "original_package_id": "rtl_generation_smoke_v001",
        "original_status": "failed_partial_package",
        "original_consumable": False,
        "recovery_package": "rtl_generation_smoke_v001_retry_01",
        "recovery_reason": "packaging_source_defect",
        "source_defects_fixed": True,
        "regression_tests_passed": True,
        "synthetic_end_to_end_passed": True,
        "canonical_inputs_preflighted": True,
        "original_partial_package_preserved": True,
        "authorized_invocation_count": 1,
    }) + "\n", encoding="utf-8")

    output = tmp_path / "retry"
    result, code = package_verified_rtl_generation_dataset(
        fixture["tasks"],
        fixture["candidates"],
        fixture["attempts"],
        fixture["split"],
        output,
        evidence_path=fixture["evidence"],
        sidecar_path=fixture["sidecar"],
        expected_rtlbench_commit=COMMIT,
        package_id="rtl_generation_smoke_v001_retry_01",
        parent_package_id="rtl_generation_smoke_v001",
        recovery_reason="packaging_source_defect",
        recovery_authorization_path=authorization,
        require_recovery_lineage=True,
    )

    assert code == 0, result
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["lineage"]["status"] == "pending_finalization"
    assert manifest["lineage"]["consumable"] is False
    report, validation_code = validate_generation_sft_package(
        output,
        expected_package_id="rtl_generation_smoke_v001_retry_01",
        expected_parent_package_id="rtl_generation_smoke_v001",
        expected_recovery_reason="packaging_source_defect",
        require_recovery_lineage=True,
        require_consumable=False,
    )
    assert validation_code == 0, report
    assert report["consumable"] is False


def test_qualification_report_hash_is_bound_to_canonical_report(tmp_path, monkeypatch) -> None:
    fixture = _write_verified_fixture(tmp_path)
    output = tmp_path / "package"
    result, code = package_verified_rtl_generation_dataset(
        fixture["tasks"],
        fixture["candidates"],
        fixture["attempts"],
        fixture["split"],
        output,
        evidence_path=fixture["evidence"],
        sidecar_path=fixture["sidecar"],
        expected_rtlbench_commit=COMMIT,
    )
    assert code == 0, result

    qualification = tmp_path / "qualification.json"
    qualification.write_text("{}\n", encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text("{}\n", encoding="utf-8")
    qualification_hash = hashlib.sha256(qualification.read_bytes()).hexdigest()
    binding_hash = hashlib.sha256(binding.read_bytes()).hexdigest()

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["asset_qualification_sha256"] = qualification_hash
    manifest["qualification_report_sha256"] = qualification_hash
    manifest["qualification_binding_sha256"] = binding_hash
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    from scripts.dataset import rtl_generation_qualification_binding, rtl_generation_smoke_run

    monkeypatch.setattr(
        rtl_generation_smoke_run,
        "validate_qualification_report_metadata",
        lambda path: ({"qualification_passed": True}, []),
    )
    monkeypatch.setattr(
        rtl_generation_qualification_binding,
        "validate_binding_report",
        lambda path: {"qualification_report_sha256": qualification_hash},
    )

    report, validation_code = validate_generation_sft_package(
        output,
        asset_qualification_path=qualification,
        qualification_binding_path=binding,
    )
    assert validation_code == 0, report
    assert report["qualification_report_hash_match"] is True

    manifest["qualification_report_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    report, validation_code = validate_generation_sft_package(
        output,
        asset_qualification_path=qualification,
        qualification_binding_path=binding,
    )
    assert validation_code == 1
    assert report["qualification_report_hash_match"] is False
    assert any("asset qualification hash" in error for error in report["errors"])


def test_five_row_synthetic_recovery_completes_packager_validator_path(tmp_path) -> None:
    fixture = _write_five_verified_fixture(tmp_path)
    authorization = tmp_path / "five-authorization.json"
    authorization.write_text(json.dumps({
        "schema_version": "rtl_dataset_packaging_recovery_authorization_v0.1",
        "authorization_status": "one_recovery_invocation_authorized",
        "original_package_id": "rtl_generation_smoke_v001",
        "original_status": "failed_partial_package",
        "original_consumable": False,
        "recovery_package": "rtl_generation_smoke_v001_retry_01",
        "recovery_reason": "packaging_source_defect",
        "source_defects_fixed": True,
        "regression_tests_passed": True,
        "synthetic_end_to_end_passed": True,
        "canonical_inputs_preflighted": True,
        "original_partial_package_preserved": True,
        "authorized_invocation_count": 1,
    }) + "\n", encoding="utf-8")

    output = tmp_path / "five-retry"
    result, code = package_verified_rtl_generation_dataset(
        fixture["tasks"],
        fixture["candidates"],
        fixture["attempts"],
        fixture["split"],
        output,
        evidence_path=fixture["evidence"],
        sidecar_path=fixture["sidecar"],
        expected_rtlbench_commit=COMMIT,
        package_id="rtl_generation_smoke_v001_retry_01",
        parent_package_id="rtl_generation_smoke_v001",
        recovery_reason="packaging_source_defect",
        recovery_authorization_path=authorization,
        require_recovery_lineage=True,
    )
    assert code == 0, result

    rows = [
        json.loads(line)
        for line in (output / "all.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["source_id"] for row in rows] == list(SMOKE_SOURCE_IDS)
    assert [row["task_id"] for row in rows] == list(SMOKE_TASK_IDS)
    report, validation_code = validate_generation_sft_package(
        output,
        expected_package_id="rtl_generation_smoke_v001_retry_01",
        expected_parent_package_id="rtl_generation_smoke_v001",
        expected_recovery_reason="packaging_source_defect",
        require_recovery_lineage=True,
        require_consumable=False,
    )
    assert validation_code == 0, report
    assert report["row_count"] == 5
    assert report["train_count"] == 5
    assert report["rejected_count"] == 0
    assert report["consumable"] is False


def test_package_requires_asset_qualification_before_writing(tmp_path) -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    candidate_id = f"{task['task_id']}_attempt_01"
    record = {
        "schema_version": "rtl_teacher_candidate_record_v0.1",
        "candidate_id": candidate_id,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 1,
        "packet_id": "packet",
        "candidate_sha256": candidate_hash,
        "candidate": candidate,
    }
    attempt = _attempt(candidate_id, task, candidate_hash)
    tasks_path = tmp_path / "tasks.jsonl"
    candidates_path = tmp_path / "candidates.jsonl"
    attempts_path = tmp_path / "attempts.jsonl"
    split_path = tmp_path / "split.json"
    tasks_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
    candidates_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    attempts_path.write_text(json.dumps(attempt) + "\n", encoding="utf-8")
    split_path.write_text(json.dumps(_split(task, tasks_path)) + "\n", encoding="utf-8")

    result, code = package_verified_rtl_generation_dataset(
        tasks_path,
        candidates_path,
        attempts_path,
        split_path,
        tmp_path / "package",
        require_asset_qualification=True,
    )

    assert code == 1
    assert any("asset qualification report is required" in error for error in result["errors"])
    assert not (tmp_path / "package" / "manifest.json").exists()


def test_package_rejects_candidate_hash_or_verification_failure(tmp_path) -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    candidate_id = f"{task['task_id']}_attempt_01"
    record = {
        "schema_version": "rtl_teacher_candidate_record_v0.1",
        "candidate_id": candidate_id,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 1,
        "packet_id": "packet",
        "candidate_sha256": "0" * 64,
        "candidate": candidate,
    }
    attempt = _attempt(candidate_id, task, candidate_hash)
    attempt["accepted"] = False
    attempt["failure_category"] = "functional_mismatch"
    attempt["checks"]["simulation"]["candidate_passes"]["passed"] = False
    attempt["checks"]["simulation"]["candidate_passes"]["reason"] = "functional_mismatch"
    attempt["mismatch_summary"]["reported_counts"] = [1]
    attempt["mismatch_summary"]["reported_sample_counts"] = [1]
    attempt["mismatch_summary"]["maximum_count"] = 1
    for name, value in (("tasks", task), ("candidates", record), ("attempts", attempt)):
        (tmp_path / f"{name}.jsonl").write_text(json.dumps(value) + "\n", encoding="utf-8")
    split = _split(task, tmp_path / "tasks.jsonl")
    (tmp_path / "split.json").write_text(json.dumps(split) + "\n", encoding="utf-8")
    result, code = package_verified_rtl_generation_dataset(
        tmp_path / "tasks.jsonl", tmp_path / "candidates.jsonl", tmp_path / "attempts.jsonl", tmp_path / "split.json", tmp_path / "package", strict=False,
    )
    assert code == 1
    assert any("candidate_sha256" in error for error in result["errors"])


def test_package_accepts_evidence_and_sidecar_directories(tmp_path) -> None:
    task = _task()
    candidate, candidate_hash = _candidate(task)
    candidate_id = f"{task['task_id']}_attempt_01"
    record = {
        "schema_version": "rtl_teacher_candidate_record_v0.1",
        "candidate_id": candidate_id,
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": 1,
        "packet_id": "packet",
        "candidate_sha256": candidate_hash,
        "candidate": candidate,
    }
    attempt = _attempt(candidate_id, task, candidate_hash)
    evidence = _evidence(attempt, candidate_hash)
    handoff = tmp_path / "verification" / "attempt_01"
    handoff.mkdir(parents=True)
    evidence_path = handoff / "candidate_evidence.jsonl"
    evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
    sidecar_path = handoff / "candidate_evidence.jsonl.runner.json"
    sidecar_path.write_text(json.dumps({
        "schema_version": "rtlbench_runner_identity_v0.2",
        "profile": "pilot-docker",
        "runtime": "docker",
        "runtime_mode": "rootful-daemon",
        "rootless": False,
        "image_identity_kind": "local-image-id",
        "image": "sha256:" + "d" * 64,
        "image_id": "sha256:" + "d" * 64,
        "image_digest": None,
        "rtlbench_commit": COMMIT,
        "runner_config_version": "rtlbench_rootless_runner_v0.1",
        "manifest_sha256": "e" * 64,
        "workspace_tree_sha256": "f" * 64,
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "partial_evidence_sha256": None,
    }) + "\n", encoding="utf-8")
    for name, value in (("tasks", task), ("candidates", record), ("attempts", attempt)):
        (tmp_path / f"{name}.jsonl").write_text(json.dumps(value) + "\n", encoding="utf-8")
    (tmp_path / "split.json").write_text(json.dumps(_split(task, tmp_path / "tasks.jsonl")) + "\n", encoding="utf-8")
    result, code = package_verified_rtl_generation_dataset(
        tmp_path / "tasks.jsonl",
        tmp_path / "candidates.jsonl",
        tmp_path / "attempts.jsonl",
        tmp_path / "split.json",
        tmp_path / "package",
        evidence_path=tmp_path / "verification",
        sidecar_path=tmp_path / "verification",
        expected_rtlbench_commit=COMMIT,
    )
    assert code == 0, result
