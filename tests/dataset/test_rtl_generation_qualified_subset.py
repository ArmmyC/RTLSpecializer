from __future__ import annotations

from scripts.dataset.rtl_generation_qualified_subset import (
    BATCH20_SOURCE_IDS,
    _qualification_rows,
)


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
