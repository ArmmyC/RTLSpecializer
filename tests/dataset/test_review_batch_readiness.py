from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from scripts.dataset.check_review_batch_readiness import main
from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.review_readiness import check_review_readiness, load_review_files, write_readiness_reports
from tests.dataset.conftest import write_rows


ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / "tests" / "fixtures" / "public_review" / "draft_rows.jsonl"
REVIEWED = ROOT / "tests" / "fixtures" / "public_review" / "reviewed_rows.jsonl"


def _row(path: Path) -> dict:
    loaded, problems = load_jsonl(path)
    assert not problems
    return loaded[0][1]


def test_identical_rows_are_not_ready() -> None:
    row = _row(DRAFT)
    result = check_review_readiness([row], [deepcopy(row)])
    assert result["ready_rows"] == 0
    assert result["rows"][0]["changed_from_selected"] is False
    assert result["rows"][0]["is_stub_answer"] is True


def test_edited_non_stub_row_is_ready() -> None:
    result = check_review_readiness([_row(DRAFT)], [_row(REVIEWED)])
    assert result["all_rows_ready"] is True
    assert result["rows"][0]["ready"] is True


def test_missing_reviewed_row_is_reported() -> None:
    selected = _row(DRAFT)
    second = deepcopy(selected)
    second["id"] = "public_verilog_eval_counter_002"
    result = check_review_readiness([selected, second], [_row(REVIEWED)])
    assert result["missing_reviewed_rows"] == [second["id"]]


def test_extra_reviewed_row_is_reported() -> None:
    reviewed = _row(REVIEWED)
    extra = deepcopy(reviewed)
    extra["id"] = "public_verilog_eval_counter_extra"
    result = check_review_readiness([_row(DRAFT)], [reviewed, extra])
    assert result["extra_reviewed_rows"] == [extra["id"]]


def test_duplicate_reviewed_ids_fail() -> None:
    reviewed = _row(REVIEWED)
    result = check_review_readiness([_row(DRAFT)], [reviewed, deepcopy(reviewed)])
    assert result["ok"] is False
    assert result["duplicate_reviewed_ids"] == [reviewed["id"]]
    assert any("duplicate reviewed row id" in error for error in result["errors"])


def test_invalid_reviewed_row_is_reported(tmp_path) -> None:
    invalid = _row(REVIEWED)
    del invalid["dataset_version"]
    selected_path = write_rows(tmp_path / "selected.jsonl", [_row(DRAFT)])
    reviewed_path = write_rows(tmp_path / "reviewed.jsonl", [invalid])
    loaded = load_review_files(selected_path, reviewed_path)
    result = check_review_readiness(
        loaded.selected_rows, loaded.reviewed_rows,
        selected_validation_errors=loaded.selected_errors_by_id,
        reviewed_validation_errors=loaded.reviewed_errors_by_id,
        selected_file_errors=loaded.selected_errors,
        reviewed_file_errors=loaded.reviewed_errors,
        selected_file_warnings=loaded.selected_warnings,
        reviewed_file_warnings=loaded.reviewed_warnings,
    )
    assert result["ok"] is False
    assert any("dataset_version" in error for error in result["rows"][0]["validation_errors"])


def test_json_report_is_written_and_parseable(tmp_path) -> None:
    output = tmp_path / "readiness.json"
    result = check_review_readiness([_row(DRAFT)], [_row(REVIEWED)])
    write_readiness_reports(result, output, None)
    assert json.loads(output.read_text(encoding="utf-8"))["ready_rows"] == 1


def test_markdown_report_has_next_action_guidance(tmp_path) -> None:
    output = tmp_path / "readiness.md"
    row = _row(DRAFT)
    result = check_review_readiness([row], [deepcopy(row)])
    write_readiness_reports(result, None, output)
    markdown = output.read_text(encoding="utf-8")
    assert "Rows needing work" in markdown
    assert "Manually edit" in markdown
    assert "promote_reviewed_rows.py" in markdown


def test_strict_cli_fails_when_row_needs_work(tmp_path) -> None:
    assert main([
        "--selected", str(DRAFT), "--reviewed", str(DRAFT),
        "--output-json", str(tmp_path / "report.json"), "--strict", "--json",
    ]) == 1


def test_cli_json_output_is_parseable() -> None:
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/check_review_batch_readiness.py",
            "--selected", str(DRAFT), "--reviewed", str(REVIEWED), "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["ready_rows"] == 1
