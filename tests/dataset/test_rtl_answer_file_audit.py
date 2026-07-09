from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_answer_file_audit import (
    CANONICAL_ANSWER_SCHEMA_VERSION,
    discover_answer_files,
    repair_answer_files,
    validate_answer_files,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _answer(source_id: str = "answer_001", **overrides) -> dict:
    row = {
        "schema_version": CANONICAL_ANSWER_SCHEMA_VERSION,
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "issue_summary": [{
            "issue": "The candidate reset path is reviewed by text inspection only.",
            "severity": "medium",
            "evidence": {
                "signal_names": ["reset_n"],
                "code_location": {"module": "dut", "block": "always", "line_range": [3, 5]},
                "reason": "Text inspection only; no checks were run.",
            },
        }],
        "time_reasoning": {"reset_behavior_risk": "Text inspection only."},
        "space_reasoning": {
            "area_risk": "No synthesis report is supplied.",
            "activity_risk": "No toggle report is supplied.",
            "hardware_resources_involved": ["reset logic"],
        },
        "safe_optimization": {
            "recommendation": "Keep the answer conservative.",
            "patch_style": "explanation_only",
            "requires_spec_confirmation": True,
        },
        "functional_risk": ["Reset behavior may differ."],
        "verification_plan": ["Run checks before making verification claims."],
        "claim_levels": {
            "correctness": "suggestion_only",
            "area": "insufficient_evidence",
            "activity": "insufficient_evidence",
            "power": "insufficient_evidence",
        },
        "evidence_used": ["artifacts.before_rtl_code", "artifacts.rtl_code", "tool_checks"],
        "limitations": ["tool_checks are null, so checks were not run."],
    }
    row.update(overrides)
    return row


def test_reads_json_wrapper_files(tmp_path) -> None:
    path = tmp_path / "batch_answers_rtl_answer_v0_1.json"
    path.write_text(json.dumps({"answers": [_answer()]}), encoding="utf-8")

    files, errors = discover_answer_files(inputs=[path])

    assert errors == []
    assert len(files) == 1
    assert files[0]["kind"] == "json_wrapper"
    assert files[0]["rows"][0]["source_id"] == "answer_001"


def test_reads_jsonl_files(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.jsonl"
    _write_jsonl(path, [_answer("a"), _answer("b")])

    files, errors = discover_answer_files(inputs=[path])

    assert errors == []
    assert files[0]["kind"] == "jsonl"
    assert [row["source_id"] for row in files[0]["rows"]] == ["a", "b"]


def test_rejects_duplicate_source_id(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.jsonl"
    _write_jsonl(path, [_answer("dup"), _answer("dup")])

    result, code = validate_answer_files(inputs=[path], strict=True)

    assert code == 1
    assert any(issue["code"] == "duplicate_source_id" for issue in result["errors"])


def test_flags_generic_signal_names(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.jsonl"
    answer = _answer()
    answer["issue_summary"][0]["evidence"]["signal_names"] = ["edge-triggered register", "reset_n"]
    _write_jsonl(path, [answer])

    result, code = validate_answer_files(inputs=[path], strict=True)

    assert code == 1
    assert any(issue["code"] == "generic_signal_name" for issue in result["errors"])


def test_repairs_generic_signal_names_by_moving_labels_to_hardware_resources(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.json"
    answer = _answer()
    answer["issue_summary"][0]["evidence"]["signal_names"] = ["mux/output assignment", "y"]
    path.write_text(json.dumps({"answers": [answer]}), encoding="utf-8")

    result, code = repair_answer_files(inputs=[path], output_dir=tmp_path / "patched")

    assert code == 0, result
    patched = _read_json(tmp_path / "patched" / path.name)["answers"][0]
    assert patched["issue_summary"][0]["evidence"]["signal_names"] == ["y"]
    assert "mux/output assignment" in patched["space_reasoning"]["hardware_resources_involved"]


def test_adds_tool_checks_to_evidence_used_when_limitations_mention_null_tool_checks(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.json"
    answer = _answer(evidence_used=["artifacts.rtl_code"])
    path.write_text(json.dumps({"answers": [answer]}), encoding="utf-8")

    result, code = repair_answer_files(inputs=[path], output_dir=tmp_path / "patched")

    assert code == 0, result
    patched = _read_json(tmp_path / "patched" / path.name)["answers"][0]
    assert "tool_checks" in patched["evidence_used"]


def test_downgrades_unsupported_claim_levels_without_evidence(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.json"
    answer = _answer(claim_levels={
        "correctness": "verified",
        "area": "tool_supported",
        "activity": "tool_supported",
        "power": "tool_supported",
    })
    path.write_text(json.dumps({"answers": [answer]}), encoding="utf-8")

    result, code = repair_answer_files(inputs=[path], output_dir=tmp_path / "patched")

    assert code == 0, result
    patched = _read_json(tmp_path / "patched" / path.name)["answers"][0]
    assert patched["claim_levels"] == {
        "correctness": "suggestion_only",
        "area": "insufficient_evidence",
        "activity": "insufficient_evidence",
        "power": "insufficient_evidence",
    }


def test_preserves_original_files_when_not_in_place(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.json"
    original = {"answers": [_answer(schema_version="rtl_answer_v0.1")]}
    path.write_text(json.dumps(original), encoding="utf-8")

    result, code = repair_answer_files(inputs=[path], output_dir=tmp_path / "patched")

    assert code == 0, result
    assert _read_json(path) == original
    assert _read_json(tmp_path / "patched" / path.name)["answers"][0]["schema_version"] == CANONICAL_ANSWER_SCHEMA_VERSION


def test_writes_patched_files_to_output_directory(tmp_path) -> None:
    source_dir = tmp_path / "source"
    path = source_dir / "nested" / "answers_rtl_answer_v0_1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"answers": [_answer(schema_version="rtl_answer_v0.1")]}), encoding="utf-8")

    result, code = repair_answer_files(input_dirs=[source_dir], output_dir=tmp_path / "patched")

    assert code == 0, result
    assert (tmp_path / "patched" / "nested" / path.name).exists()


def test_generates_markdown_and_json_reports(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.json"
    path.write_text(json.dumps({"answers": [_answer()]}), encoding="utf-8")
    report_md = tmp_path / "report.md"
    report_json = tmp_path / "report.json"

    result, code = validate_answer_files(inputs=[path], output_md=report_md, output_json=report_json)

    assert code == 0, result
    assert report_md.read_text(encoding="utf-8").startswith("# RTL Answer File Validation")
    assert _read_json(report_json)["answers_scanned"] == 1


def test_flags_suspicious_mutation_label_mismatch_without_auto_changing_it(tmp_path) -> None:
    path = tmp_path / "answers_rtl_answer_v0_1.json"
    answer = _answer(
        source_id="row_synthetic_off_by_one_counter_limit",
        issue_summary=[{
            "issue": "The candidate reset polarity is inverted.",
            "severity": "high",
            "evidence": {
                "signal_names": ["reset_n"],
                "code_location": {"module": "dut", "block": "always", "line_range": [4, 4]},
                "reason": "The text discusses reset polarity only.",
            },
        }],
    )
    path.write_text(json.dumps({"answers": [answer]}), encoding="utf-8")

    result, code = repair_answer_files(inputs=[path], output_dir=tmp_path / "patched", strict=True)

    assert code == 1
    assert any(issue["code"] == "suspicious_mutation_label_mismatch" for issue in result["manual_review_flags"])
    patched = _read_json(tmp_path / "patched" / path.name)["answers"][0]
    assert patched["source_id"] == "row_synthetic_off_by_one_counter_limit"
    assert patched["issue_summary"][0]["issue"] == "The candidate reset polarity is inverted."
