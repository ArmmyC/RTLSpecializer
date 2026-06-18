from __future__ import annotations

import json
import re

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.validation import (
    artifact_has_substantive_rtl,
    extract_module_names,
    has_passing_tool_evidence,
    has_tool_evidence,
    tool_check_status,
    validate_dataset_file,
)
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


def test_substantive_rtl_heuristic_accepts_counter_and_mux() -> None:
    counter = "module c(input logic clk, output logic [3:0] q); always_ff @(posedge clk) q <= q + 1'b1; endmodule"
    mux = "module m(input wire s, a, b, output wire y); assign y = s ? a : b; endmodule"
    assert artifact_has_substantive_rtl(counter)
    assert artifact_has_substantive_rtl(mux)


def test_substantive_rtl_heuristic_rejects_empty_placeholder() -> None:
    assert not artifact_has_substantive_rtl("module sample_0; // synthetic illustrative RTL\nendmodule")
    assert not artifact_has_substantive_rtl("module empty; // comments only\nendmodule")


def test_extract_module_names() -> None:
    assert extract_module_names("module alpha; endmodule\nmodule beta(input wire x); endmodule") == ["alpha", "beta"]


def test_reviewed_golden_placeholder_fails(tmp_path, valid_row) -> None:
    valid_row["messages"][1]["content"]["artifacts"]["rtl_code"] = "module empty; // comments only\nendmodule"
    report = validate_dataset_file(write_rows(tmp_path / "bad.jsonl", [valid_row]))
    assert any("substantive RTL" in item.message for item in report.errors)


def test_reviewed_golden_substantive_rtl_passes(tmp_path, valid_row) -> None:
    report = validate_dataset_file(write_rows(tmp_path / "good.jsonl", [valid_row]), strict=True)
    assert report.ok, [item.format() for item in report.errors]


def test_processed_draft_fails_but_unsplit_draft_passes(tmp_path, valid_row) -> None:
    valid_row["review_status"] = "draft"
    valid_row["split"] = "train"
    report = validate_dataset_file(write_rows(tmp_path / "train.jsonl", [valid_row]))
    assert any(item.field == "review_status" and "validated or reviewed" in item.message for item in report.errors)
    valid_row["split"] = "unsplit"
    report = validate_dataset_file(write_rows(tmp_path / "unsplit.jsonl", [valid_row]), strict=True)
    assert report.ok


def test_golden_ids_and_rtl_evidence_are_grounded() -> None:
    rows, problems = load_jsonl(GOLDEN)
    assert not problems
    for _, row in rows:
        assert re.fullmatch(r"golden_[a-z0-9_]+_(?:bug|activity|report|reject|compare)_\d{3}", row["id"])
        task = row["messages"][1]["content"]
        answer = row["messages"][2]["content"]
        rtl = "\n".join(value for key, value in task["artifacts"].items() if key in {"rtl_code", "before_rtl_code", "after_rtl_code"} and value)
        if not rtl:
            continue
        modules = set(extract_module_names(rtl))
        for issue in answer["issue_summary"]:
            evidence = issue["evidence"]
            assert evidence["signal_names"]
            assert evidence["code_location"]["module"] in modules


def _tool_check(status: str, summary: str = "Synthetic check result") -> dict:
    return {"status": status, "tool": "synthetic_fixture", "version": None, "summary": summary, "artifact_ref": None}


def test_verified_correctness_rejects_unknown_fail_and_not_run(tmp_path, valid_row) -> None:
    valid_row["messages"][2]["content"]["claim_levels"]["correctness"] = "verified"
    for status in ("unknown", "fail", "not_run"):
        valid_row["tool_checks"]["simulation"] = _tool_check(status)
        report = validate_dataset_file(write_rows(tmp_path / f"{status}.jsonl", [valid_row]))
        assert any("verified requires a passing simulation or equivalence check" in item.message for item in report.errors)


def test_verified_correctness_accepts_simulation_or_equivalence_pass(tmp_path, valid_row) -> None:
    valid_row["messages"][2]["content"]["claim_levels"]["correctness"] = "verified"
    valid_row["tool_checks"]["simulation"] = _tool_check("pass")
    assert validate_dataset_file(write_rows(tmp_path / "simulation.jsonl", [valid_row])).ok
    valid_row["tool_checks"]["simulation"] = None
    valid_row["tool_checks"]["equivalence"] = _tool_check("pass")
    assert validate_dataset_file(write_rows(tmp_path / "equivalence.jsonl", [valid_row])).ok


def test_tool_supported_rejects_null_not_run_and_empty_evidence(tmp_path, valid_row) -> None:
    valid_row["messages"][2]["content"]["claim_levels"]["activity"] = "tool_supported"
    for name, check in (("null", None), ("not_run", _tool_check("not_run")), ("empty", _tool_check("pass", ""))):
        valid_row["tool_checks"]["toggle"] = check
        report = validate_dataset_file(write_rows(tmp_path / f"{name}.jsonl", [valid_row]))
        assert any("tool_supported requires meaningful toggle evidence" in item.message for item in report.errors)


def test_unknown_report_evidence_supports_conservative_tool_claim(tmp_path) -> None:
    rows, problems = load_jsonl(GOLDEN)
    assert not problems
    row = next(row for _, row in rows if row["id"].startswith("golden_timer_report"))
    assert tool_check_status(row, "toggle") == "unknown"
    assert has_tool_evidence(row, "toggle")
    assert not has_passing_tool_evidence(row, "toggle")
    assert validate_dataset_file(write_rows(tmp_path / "toggle.jsonl", [row]), strict=True).ok


def test_golden_synthesis_report_keeps_area_conservative() -> None:
    rows, problems = load_jsonl(GOLDEN)
    assert not problems
    row = next(row for _, row in rows if row["id"].startswith("golden_fsm_report"))
    assert row["messages"][2]["content"]["claim_levels"]["area"] == "insufficient_evidence"
