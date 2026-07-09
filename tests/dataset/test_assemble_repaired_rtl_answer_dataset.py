from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_answer_dataset_assembly import assemble_repaired_rtl_answer_dataset
from scripts.dataset.rtl_answer_file_audit import CANONICAL_ANSWER_SCHEMA_VERSION, NO_TOOL_CHECKS_LIMITATION


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _task(source_id: str) -> dict:
    return {
        "schema_version": "rtl_task.v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "prompt": f"Review candidate RTL for {source_id}.",
        "artifacts": {
            "rtl_code": (
                "module dut(input clk, input reset_n, output logic y);\n"
                "always_ff @(posedge clk or negedge reset_n) begin\n"
                "  if (!reset_n) y <= 1'b0;\n"
                "  else y <= 1'b1;\n"
                "end\n"
                "endmodule\n"
            ),
            "before_rtl_code": (
                "module dut(input clk, input reset_n, output logic y);\n"
                "always_ff @(posedge clk or posedge reset_n) begin\n"
                "  if (reset_n) y <= 1'b0;\n"
                "  else y <= 1'b1;\n"
                "end\n"
                "endmodule\n"
            ),
        },
        "design_context": {
            "target_module_name": "dut",
            "rtl_module_name": "dut",
            "prompt_embedded_candidate_rtl": True,
        },
        "tool_checks": {
            "equivalence": None,
            "lint": None,
            "power": None,
            "simulation": None,
            "synthesis": None,
            "toggle": None,
        },
        "synthetic_bug": True,
        "mutation_summary": "Candidate reset polarity may be inverted.",
        "mutated_signal_names": ["reset_n"],
        "license": "unconfirmed_upstream_license",
        "design_family": "sequential",
        "provenance": {"origin": "external_rtlcoder_gpt_generated_unverified"},
    }


def _answer(source_id: str, **overrides) -> dict:
    row = {
        "schema_version": CANONICAL_ANSWER_SCHEMA_VERSION,
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "issue_summary": [{
            "issue": "Candidate reset behavior may differ under active-high reset handling.",
            "severity": "medium",
            "evidence": {
                "signal_names": ["reset_n"],
                "code_location": {"module": "dut", "block": "always_ff", "line_range": [2, 4]},
                "reason": "Reviewed by text inspection only; tool_checks are null.",
            },
        }],
        "time_reasoning": {"latency_risk": "Text inspection only; no timing claims are made."},
        "space_reasoning": {
            "area_risk": "No synthesis or power reports are supplied.",
            "activity_risk": "No toggle or activity reports are supplied.",
            "hardware_resources_involved": ["reset logic"],
        },
        "safe_optimization": {
            "recommendation": "Keep the analysis conservative and draft-only.",
            "patch_style": "explanation_only",
            "requires_spec_confirmation": True,
        },
        "functional_risk": ["Reset behavior may not match the reference RTL."],
        "verification_plan": ["Run parse, lint, simulation, and synthesis checks before trusting the candidate RTL."],
        "claim_levels": {
            "correctness": "suggestion_only",
            "area": "insufficient_evidence",
            "activity": "insufficient_evidence",
            "power": "insufficient_evidence",
        },
        "evidence_used": [
            "artifacts.before_rtl_code",
            "artifacts.rtl_code",
            "mutation_summary",
            "mutated_signal_names",
            "prompt",
            "tool_checks",
        ],
        "limitations": [NO_TOOL_CHECKS_LIMITATION],
    }
    row.update(overrides)
    return row


def _assemble(tmp_path: Path, *, strict: bool = False) -> tuple[Path, Path, Path]:
    output_path = tmp_path / "assembled.jsonl"
    report_md = tmp_path / "assembly_report.md"
    report_json = tmp_path / "assembly_report.json"
    return output_path, report_md, report_json


def test_reads_wrapper_files_writes_one_combined_jsonl_and_preserves_task_order(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_dir = tmp_path / "answers"
    output_path, report_md, report_json = _assemble(tmp_path, strict=True)
    _write_jsonl(tasks_path, [_task("row_b"), _task("row_a")])
    _write_json(answers_dir / "batch_001_answers_rtl_answer_v0_1.json", {"answers": [_answer("row_a"), _answer("row_b")]})

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_dir,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
        strict=True,
    )

    assert code == 0, result
    assert [row["source_id"] for row in _read_jsonl(output_path)] == ["row_b", "row_a"]
    assert report_md.read_text(encoding="utf-8").startswith("# RTL Answer Dataset Assembly")
    assert _read_json(report_json)["output_sha256"] == result["output_sha256"]
    assert len(result["output_sha256"]) == 64


def test_skips_chat_rows_and_ignores_report_files(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_dir = tmp_path / "answers"
    output_path, report_md, report_json = _assemble(tmp_path)
    _write_jsonl(tasks_path, [_task("row_001")])
    _write_json(answers_dir / "batch_001_answers_rtl_answer_v0_1.json", {"answers": [_answer("row_001")]})
    _write_json(
        answers_dir / "chat_rtl_answer_v0_1.json",
        {"messages": [{"role": "system", "content": "draft"}, {"role": "assistant", "content": {"note": "skip"}}]},
    )
    _write_json(answers_dir / "daily_rtl_answer_v0_1_report.json", {"answers": [_answer("ignored")]})

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_dir,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
    )

    assert code == 0, result
    assert result["files_scanned"] == 2
    assert any(item["kind"] == "skipped_chat_row" and item["answers"] == 0 for item in result["file_summaries"])
    assert all("report" not in item["path"] for item in result["file_summaries"])
    assert [row["source_id"] for row in _read_jsonl(output_path)] == ["row_001"]


def test_selects_repaired_file_over_original_batch_and_identical_duplicates_are_harmless(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_root = tmp_path / "answer_sets"
    output_path, report_md, report_json = _assemble(tmp_path)
    row = _answer("dup_row")
    _write_jsonl(tasks_path, [_task("dup_row")])
    original_path = answers_root / "teacher_answer_returns" / "batch_001_answers_rtl_answer_v0_1.json"
    repaired_path = answers_root / "repaired_rtl_answer_batches" / "teacher_answer_returns" / "batch_001_answers_rtl_answer_v0_1.json"
    original_text = json.dumps({"answers": [row]}, ensure_ascii=False, indent=2) + "\n"
    repaired_text = json.dumps({"answers": [row]}, ensure_ascii=False, indent=2) + "\n"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    repaired_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text(original_text, encoding="utf-8")
    repaired_path.write_text(repaired_text, encoding="utf-8")

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_root,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
        strict=True,
    )

    assert code == 0, result
    assert result["selected_source_file_by_source_id"]["dup_row"] == str(repaired_path.resolve())
    assert result["duplicate_source_id_count"] == 1
    assert result["harmless_duplicate_count"] == 1
    assert result["conflicting_duplicate_count"] == 0
    assert original_path.read_text(encoding="utf-8") == original_text
    assert repaired_path.read_text(encoding="utf-8") == repaired_text


def test_conflicting_duplicate_is_manual_review_and_non_strict_still_writes_output(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_root = tmp_path / "answer_sets"
    output_path, report_md, report_json = _assemble(tmp_path)
    _write_jsonl(tasks_path, [_task("dup_row")])
    original = _answer("dup_row", issue_summary=[{
        "issue": "Candidate reset branch appears inverted.",
        "severity": "medium",
        "evidence": {
            "signal_names": ["reset_n"],
            "code_location": {"module": "dut", "block": "always_ff", "line_range": [2, 4]},
            "reason": "Reviewed by text inspection only; tool_checks are null.",
        },
    }])
    repaired = _answer("dup_row", issue_summary=[{
        "issue": "Candidate reset polarity may invert the reset branch semantics.",
        "severity": "high",
        "evidence": {
            "signal_names": ["reset_n"],
            "code_location": {"module": "dut", "block": "always_ff", "line_range": [2, 4]},
            "reason": "Reviewed by text inspection only; tool_checks are null.",
        },
    }])
    _write_json(answers_root / "teacher_answer_returns" / "batch_001_answers_rtl_answer_v0_1.json", {"answers": [original]})
    repaired_path = answers_root / "repaired_rtl_answer_batches" / "teacher_answer_returns" / "batch_001_answers_rtl_answer_v0_1.json"
    _write_json(repaired_path, {"answers": [repaired]})

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_root,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
    )

    assert code == 0, result
    assert output_path.exists()
    assert _read_jsonl(output_path)[0]["issue_summary"][0]["severity"] == "high"
    assert result["conflicting_duplicate_count"] == 1
    assert any(flag["code"] == "duplicate_source_id_conflicting_rows" for flag in result["manual_review_flags"])
    assert result["selected_source_file_by_source_id"]["dup_row"] == str(repaired_path.resolve())


def test_strict_mode_fails_on_manual_review_flag(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_root = tmp_path / "answer_sets"
    output_path, report_md, report_json = _assemble(tmp_path)
    _write_jsonl(tasks_path, [_task("dup_row")])
    _write_json(answers_root / "teacher_answer_returns" / "batch_001_answers_rtl_answer_v0_1.json", {"answers": [_answer("dup_row")]})
    _write_json(
        answers_root / "repaired_rtl_answer_batches" / "teacher_answer_returns" / "batch_001_answers_rtl_answer_v0_1.json",
        {"answers": [_answer("dup_row", issue_summary=[{
            "issue": "Candidate reset polarity may invert the reset branch semantics.",
            "severity": "high",
            "evidence": {
                "signal_names": ["reset_n"],
                "code_location": {"module": "dut", "block": "always_ff", "line_range": [2, 4]},
                "reason": "Reviewed by text inspection only; tool_checks are null.",
            },
        }])]},
    )

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_root,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
        strict=True,
    )

    assert code == 1
    assert output_path.exists()
    assert result["safe_output"] is True
    assert result["strict_ok"] is False


def test_reports_missing_answers_and_extra_answers_without_tasks(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_dir = tmp_path / "answers"
    output_path, report_md, report_json = _assemble(tmp_path)
    _write_jsonl(tasks_path, [_task("row_001"), _task("row_002")])
    _write_json(answers_dir / "batch_001_answers_rtl_answer_v0_1.json", {"answers": [_answer("row_001"), _answer("row_999")]})

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_dir,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
    )

    assert code == 0, result
    assert result["missing_task_answer_count"] == 1
    assert result["extra_answer_without_task_count"] == 1
    assert result["missing_task_answers"] == ["row_002"]
    assert result["extra_answers_without_tasks"] == ["row_999"]
    assert [row["source_id"] for row in _read_jsonl(output_path)] == ["row_001"]


def test_validates_selected_row_against_matching_task_and_blocks_unsafe_output(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_dir = tmp_path / "answers"
    output_path, report_md, report_json = _assemble(tmp_path)
    answer = _answer("row_001", limitations=["No external tools were invoked."])
    _write_jsonl(tasks_path, [_task("row_001")])
    _write_json(answers_dir / "batch_001_answers_rtl_answer_v0_1.json", {"answers": [answer]})

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=answers_dir,
        tasks_path=tasks_path,
        output_path=output_path,
        report_md=report_md,
        report_json=report_json,
    )

    assert code == 1
    assert result["safe_output"] is False
    assert not output_path.exists()
    assert any(issue["code"] == "missing_tool_checks_limitation" for issue in result["errors"])
