from __future__ import annotations

import json

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.validation import validate_dataset_file
from tests.dataset.conftest import GOLDEN, write_rows


def test_valid_seed_dataset_passes() -> None:
    report = validate_dataset_file(GOLDEN, strict=True)
    assert report.ok
    assert report.rows == 20
    assert report.summary["by_task_type"] == {
        "rtl_area_activity_review": 5, "rtl_before_after_judgment": 3,
        "rtl_bug_review": 5, "rtl_tool_report_explanation": 3,
        "unsafe_optimization_rejection": 4,
    }


def test_missing_required_top_level_field_fails(tmp_path, valid_row) -> None:
    valid_row.pop("license")
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert not report.ok
    assert any(item.field == "license" for item in report.errors)


def test_invalid_task_type_fails(tmp_path, valid_row) -> None:
    valid_row["task_family"] = "invented"
    valid_row["messages"][1]["content"]["task_type"] = "invented"
    valid_row["messages"][2]["content"]["task_type"] = "invented"
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert not report.ok
    assert any("invalid task type" in item.message for item in report.errors)


def test_invalid_claim_level_fails(tmp_path, valid_row) -> None:
    valid_row["messages"][2]["content"]["claim_levels"]["power"] = "proved"
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert not report.ok
    assert any(item.field.endswith("claim_levels.power") for item in report.errors)


def test_duplicate_ids_are_flagged(tmp_path, valid_row) -> None:
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row, valid_row]))
    assert not report.ok
    assert any("duplicate row id" in item.message for item in report.errors)


def test_load_jsonl_ignores_blanks_and_reports_malformed_line(tmp_path, valid_row) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text("\n" + json.dumps(valid_row) + "\n{broken\n", encoding="utf-8")
    rows, problems = load_jsonl(path)
    assert len(rows) == 1
    assert problems[0].line == 3

