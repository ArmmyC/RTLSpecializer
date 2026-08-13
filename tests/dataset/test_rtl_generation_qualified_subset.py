from __future__ import annotations

import pytest

from scripts.dataset.rtl_generation_qualified_subset import (
    BATCH20_SOURCE_IDS,
    QualifiedSubsetError,
    _sha256,
    _task_id,
    _text_bytes,
    _qualification_rows,
    _normalize_qualification_output_hashes,
    _validate_source_input_alignment,
)
from scripts.dataset.rtl_generation_preparation import discover_source_rows


def _qualification_fixture() -> tuple[list[str], list[str], dict]:
    failed_positions = {"Prob092_gatesv100", "Prob112_always_case2", "Prob115_shift18", "Prob109_fsm1"}
    report_rows = []
    qualified = []
    failed = []
    for index, source_id in enumerate(BATCH20_SOURCE_IDS):
        task_id = f"task_{source_id}"
        passed = source_id not in failed_positions
        report_rows.append({
            "source_id": source_id,
            "task_id": task_id,
            "qualification_passed": passed,
            "qualification_status": "qualified" if passed else "positive_candidate_failed",
        })
        (qualified if passed else failed).append(task_id)
    return qualified, failed, {
        "schema_version": "rtl_asset_qualification_report_v0.1",
        "errors": [],
        "selected_tasks": 20,
        "rows": report_rows,
    }


def test_qualified_rows_preserve_pinned_order_and_filter_failures() -> None:
    qualified, failed, report = _qualification_fixture()
    rows, failed_rows = _qualification_rows(
        qualification_report=report,
        selected_ids=list(BATCH20_SOURCE_IDS),
        qualified_task_ids=qualified,
        failed_task_ids=failed,
    )

    assert [row["source_id"] for row in rows] == [
        source_id for source_id in BATCH20_SOURCE_IDS
        if source_id not in {"Prob092_gatesv100", "Prob112_always_case2", "Prob115_shift18", "Prob109_fsm1"}
    ]
    assert [row["source_id"] for row in failed_rows] == [
        "Prob092_gatesv100",
        "Prob112_always_case2",
        "Prob115_shift18",
        "Prob109_fsm1",
    ]


def test_qualified_rows_reject_wrong_order() -> None:
    qualified, failed, report = _qualification_fixture()
    qualified[0], qualified[1] = qualified[1], qualified[0]

    try:
        _qualification_rows(
            qualification_report=report,
            selected_ids=list(BATCH20_SOURCE_IDS),
            qualified_task_ids=qualified,
            failed_task_ids=failed,
        )
    except ValueError as exc:
        assert "qualified task list" in str(exc)
    else:
        raise AssertionError("wrong qualified order was accepted")


def test_qualified_rows_rejects_a_failed_task_in_qualified_list() -> None:
    qualified, failed, report = _qualification_fixture()
    qualified[-1] = failed[0]

    try:
        _qualification_rows(
            qualification_report=report,
            selected_ids=list(BATCH20_SOURCE_IDS),
            qualified_task_ids=qualified,
            failed_task_ids=failed,
        )
    except ValueError as exc:
        assert "qualified task list" in str(exc)
    else:
        raise AssertionError("failed task was accepted as qualified")


def test_qualified_rows_supports_a_different_passed_subset_size() -> None:
    selected = ["SourceA", "SourceB", "SourceC"]
    report_rows = [
        {
            "source_id": source_id,
            "task_id": f"Task{source_id}",
            "qualification_passed": source_id != "SourceB",
            "qualification_status": "qualified" if source_id != "SourceB" else "positive_candidate_failed",
        }
        for source_id in selected
    ]
    qualified, failed = _qualification_rows(
        qualification_report={
            "schema_version": "rtl_asset_qualification_report_v0.1",
            "errors": [],
            "selected_tasks": 3,
            "rows": report_rows,
        },
        selected_ids=selected,
        qualified_task_ids=["TaskSourceA", "TaskSourceC"],
        failed_task_ids=["TaskSourceB"],
    )
    assert [row["source_id"] for row in qualified] == ["SourceA", "SourceC"]
    assert [row["source_id"] for row in failed] == ["SourceB"]


def test_qualified_rows_accept_validator_source_id_lists() -> None:
    selected = ["SourceA", "SourceB", "SourceC"]
    report_rows = [
        {
            "source_id": source_id,
            "task_id": f"Task{source_id}",
            "qualification_passed": source_id != "SourceB",
            "qualification_status": (
                "qualified" if source_id != "SourceB" else "positive_candidate_failed"
            ),
        }
        for source_id in selected
    ]

    qualified, failed = _qualification_rows(
        qualification_report={
            "schema_version": "rtl_asset_qualification_report_v0.1",
            "errors": [],
            "selected_tasks": 3,
            "rows": report_rows,
        },
        selected_ids=selected,
        qualified_task_ids=["SourceA", "SourceC"],
        failed_task_ids=["SourceB"],
    )

    assert [row["task_id"] for row in qualified] == ["TaskSourceA", "TaskSourceC"]
    assert [row["task_id"] for row in failed] == ["TaskSourceB"]


def test_qualification_output_hashes_accept_current_artifact_names() -> None:
    digest = "a" * 64
    hashes = _normalize_qualification_output_hashes(
        {
            "raw_candidate_evidence_sha256": digest,
            "qualification_report_sha256": digest,
            "qualification_evidence_sha256": digest,
            "runner_sidecar_sha256": digest,
            "qualified_task_ids_sha256": digest,
            "failed_or_inconclusive_task_ids_sha256": digest,
        }
    )

    assert hashes["evidence_sha256"] == digest
    assert hashes["qualification_validation_report_sha256"] == digest


def test_qualification_output_hashes_accept_runner_evidence_name() -> None:
    digest = "a" * 64
    hashes = _normalize_qualification_output_hashes(
        {
            "runner_evidence_sha256": digest,
            "qualification_report_sha256": digest,
            "qualification_evidence_sha256": digest,
            "runner_sidecar_sha256": digest,
            "qualified_task_ids_sha256": digest,
            "failed_or_inconclusive_task_ids_sha256": digest,
        }
    )

    assert hashes["evidence_sha256"] == digest


def test_qualification_output_hashes_reject_disagreeing_aliases() -> None:
    digest = "a" * 64
    with pytest.raises(QualifiedSubsetError, match="aliases disagree"):
        _normalize_qualification_output_hashes(
            {
                "evidence_sha256": "b" * 64,
                "raw_candidate_evidence_sha256": digest,
                "qualification_validation_report_sha256": digest,
                "qualification_evidence_sha256": digest,
                "runner_sidecar_sha256": digest,
                "qualified_task_ids_sha256": digest,
                "failed_or_inconclusive_task_ids_sha256": digest,
            }
        )


def _source_alignment_fixture(tmp_path):
    source_root = tmp_path / "checkout"
    source_input = source_root / "dataset_spec-to-rtl"
    source_input.mkdir(parents=True)
    source_id = "Prob001_zero"
    prompt = "\nCreate a module with input a and output zero.\n"
    reference = "module RefModule(input a, output zero); assign zero = 1'b0; endmodule\n"
    testbench = (
        'module tb; TopModule dut(); initial begin '
        '$display("Mismatches: %0d", 0); $finish; end endmodule\n'
    )
    (source_input / f"{source_id}_prompt.txt").write_text(prompt, encoding="utf-8")
    (source_input / f"{source_id}_ref.sv").write_text(reference, encoding="utf-8")
    (source_input / f"{source_id}_test.sv").write_text(testbench, encoding="utf-8")
    rows, errors = discover_source_rows(source_input)
    assert errors == []
    row = rows[0]
    row.source_commit = "a" * 40
    row.provenance["source_commit"] = row.source_commit
    inventory = {
        source_id: {
            "source_dataset": row.source_dataset,
            "source_commit": "a" * 40,
            "task_id": _task_id(row),
            "source_prompt_sha256": _sha256(_text_bytes(row.specification)),
            "reference_rtl_sha256": _sha256(_text_bytes(row.reference_rtl)),
            "testbench_sha256": _sha256(_text_bytes(row.testbench)),
        }
    }
    return source_root, source_input, inventory, source_id


def test_source_input_alignment_accepts_attested_view(tmp_path) -> None:
    source_root, source_input, inventory, source_id = _source_alignment_fixture(tmp_path)

    _validate_source_input_alignment(
        source_input=source_input,
        source_root=source_root,
        inventory=inventory,
        selected_ids=[source_id],
    )


def test_source_input_alignment_rejects_changed_public_source(tmp_path) -> None:
    source_root, source_input, inventory, source_id = _source_alignment_fixture(tmp_path)
    (source_input / f"{source_id}_prompt.txt").write_text(
        "\nchanged public specification\n",
        encoding="utf-8",
    )

    with pytest.raises(QualifiedSubsetError, match="prompt hash mismatch"):
        _validate_source_input_alignment(
            source_input=source_input,
            source_root=source_root,
            inventory=inventory,
            selected_ids=[source_id],
        )
