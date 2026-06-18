from __future__ import annotations

from scripts.dataset.validation import validate_dataset_file
from tests.dataset.conftest import write_rows


def _set_recommendation(row: dict, text: str) -> None:
    row["messages"][2]["content"]["safe_optimization"]["recommendation"] = text


def test_unsupported_power_claim_fails(tmp_path, valid_row) -> None:
    _set_recommendation(valid_row, "This reduces power.")
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert any("power improvement" in item.message for item in report.errors)


def test_unsupported_area_claim_fails(tmp_path, valid_row) -> None:
    _set_recommendation(valid_row, "Area is improved.")
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert any("area improvement" in item.message for item in report.errors)


def test_unsupported_activity_claim_fails(tmp_path, valid_row) -> None:
    _set_recommendation(valid_row, "This reduces switching activity.")
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert any("switching/toggle" in item.message for item in report.errors)


def test_unsupported_verified_correctness_claim_fails(tmp_path, valid_row) -> None:
    _set_recommendation(valid_row, "Correctness is verified.")
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert any("verified correctness" in item.message for item in report.errors)


def test_weaker_claim_language_is_allowed(tmp_path, valid_row) -> None:
    _set_recommendation(valid_row, "This may reduce switching activity and could reduce area after synthesis confirmation.")
    report = validate_dataset_file(write_rows(tmp_path / "good.jsonl", [valid_row]))
    assert report.ok

