from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.constants import TOOL_CHECKS
from scripts.dataset.rtl_answer_teacher_batches import (
    export_rtl_answer_teacher_batches,
    merge_rtl_task_answer_rows,
    validate_rtl_answer_teacher_batch,
)


ROOT = Path(__file__).resolve().parents[2]
PROMPT_TEMPLATE = ROOT / "docs" / "dataset" / "llm_rtl_answer_generation_prompt.md"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _task(source_id: str = "task_001", *, candidate: bool = False) -> dict:
    before = (
        "module TopModule(input clk, input d, output reg q);\n"
        "  always @(posedge clk) q <= ~d;\n"
        "endmodule\n"
    ) if candidate else None
    context = {
        "target_domain": "digital_rtl_public_benchmark",
        "priority": ["correctness", "low_switching_activity", "low_area"],
        "timing_policy": "timing_is_constraint_not_reward",
        "source_rtl_role": "reference_rtl",
        "target_module_name": "TopModule",
        "rtl_module_name": "RefModule",
        "interface_ports_from_prompt": ["input clk", "input d", "output q"],
    }
    if candidate:
        context["prompt_embedded_candidate_rtl"] = True
    return {
        "schema_version": "rtl_task_v0.1",
        "source_id": source_id,
        "source_dataset": "synthetic_fixture",
        "license": "fixture",
        "provenance": {"origin": "synthetic_test"},
        "design_family": "dff",
        "task_type": "rtl_bug_review",
        "user_goal": "find_correctness_bug",
        "domain": "digital_rtl",
        "prompt": "Review this DFF task.\nPreserve the interface.\n",
        "source_rtl_role": "reference_rtl",
        "tool_checks": {name: None for name in sorted(TOOL_CHECKS)},
        "design_context": context,
        "artifacts": {
            "rtl_code": "module RefModule(input clk, input d, output reg q);\n  always @(posedge clk) q <= d;\nendmodule\n",
            "before_rtl_code": before,
            "after_rtl_code": None,
            "testbench": "module tb;\n  reg clk;\nendmodule\n",
            "lint_log": None,
            "synthesis_report": None,
            "toggle_report": None,
        },
        "extracted_rtl_summary": {
            "top_module": "RefModule",
            "clock_signals": ["clk"],
            "reset_signals": [],
            "registered_signals": ["q"],
            "combinational_blocks": [],
            "suspected_fsm_signals": [],
            "suspected_counters": [],
            "unused_enable_signals": [],
            "activity_hotspots": [],
        },
        "constraints": {
            "preserve_top_level_interface": True,
            "preserve_cycle_level_behavior": True,
            "preserve_reset_behavior": True,
            "do_not_claim_power_without_power_report": True,
            "prefer_minimal_patch": True,
        },
        "notes": ["The supplied RTL is reference RTL; no candidate DUT source is provided in this normalized task."],
        "assumptions": ["No tool evidence was added during normalization."],
        "required_output": [
            "claim_levels",
            "functional_risk",
            "issue_summary",
            "safe_optimization",
            "space_reasoning",
            "time_reasoning",
            "verification_plan",
        ],
    }


def _answer(source_id: str = "task_001", *, issue: str | None = None) -> dict:
    return {
        "schema_version": "rtl_answer_v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "issue_summary": [{
            "issue": issue or "No candidate DUT-specific bug can be identified from the supplied reference-only artifacts.",
            "severity": "low",
            "evidence": {
                "signal_names": [],
                "code_location": {"module": "RefModule", "block": "text_inspection", "line_range": None},
                "reason": "Only supplied task artifacts were inspected; tool checks are null.",
            },
        }],
        "time_reasoning": {
            "clock_cycle_behavior": "No timing result is claimed.",
            "reset_behavior_risk": "Checks were not run.",
        },
        "space_reasoning": {
            "area_risk": "No synthesis report is supplied.",
            "activity_risk": "No toggle or power report is supplied.",
        },
        "safe_optimization": {
            "recommendation": "Keep this as a conservative text-inspection answer for human review.",
            "patch_style": "explanation_only",
            "requires_spec_confirmation": True,
        },
        "functional_risk": [],
        "verification_plan": [
            "Run lint or compile before making any verification claim.",
            "Run simulation only if a correctness result is needed.",
        ],
        "claim_levels": {
            "correctness": "suggestion_only",
            "area": "insufficient_evidence",
            "activity": "insufficient_evidence",
            "power": "insufficient_evidence",
        },
        "evidence_used": ["prompt", "artifacts.rtl_code", "tool_checks"],
        "limitations": ["tool_checks are null; checks were not run."],
    }


def test_exports_deterministic_teacher_batch_files(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    _write_jsonl(tasks, [_task("task_001"), _task("task_002")])
    first, first_code = export_rtl_answer_teacher_batches(tasks, tmp_path / "first", batch_size=1)
    second, second_code = export_rtl_answer_teacher_batches(tasks, tmp_path / "second", batch_size=1)
    assert first_code == second_code == 0, (first, second)
    assert (tmp_path / "first" / "batch_001.json").read_text(encoding="utf-8") == (
        tmp_path / "second" / "batch_001.json"
    ).read_text(encoding="utf-8")


def test_teacher_prompt_uses_canonical_schema_names_and_wrapper() -> None:
    text = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "rtl_task.v0.1" not in text
    assert "rtl_answer.v0.1" not in text
    assert "rtl_task_v0.1" in text
    assert "rtl_answer_v0.1" in text
    assert "number of returned answers must exactly match" in lowered
    assert "same order as the input rows" in lowered
    assert '"answers"' in text


def test_batch_size_limit_and_force_behaviour(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    _write_jsonl(tasks, [_task("task_001"), _task("task_002"), _task("task_003")])
    output_dir = tmp_path / "batches"

    result, code = export_rtl_answer_teacher_batches(tasks, output_dir, batch_size=2, limit=2)
    assert code == 0, result
    assert len(result["batch_files"]) == 1
    payload = _load_json(output_dir / "batch_001.json")
    assert payload["row_count"] == 2
    assert payload["expected_source_ids"] == ["task_001", "task_002"]

    failed, failed_code = export_rtl_answer_teacher_batches(tasks, output_dir, batch_size=1)
    assert failed_code == 1
    assert "managed teacher batch files" in failed["errors"][0]

    keep = output_dir / "reviewer_notes.md"
    keep.write_text("keep\n", encoding="utf-8")
    forced, forced_code = export_rtl_answer_teacher_batches(tasks, output_dir, batch_size=1, force=True)
    assert forced_code == 0, forced
    assert len(forced["batch_files"]) == 3
    assert keep.read_text(encoding="utf-8") == "keep\n"


def test_export_preserves_multiline_rtl_and_testbench_text(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    row = _task("task_001")
    _write_jsonl(tasks, [row])
    result, code = export_rtl_answer_teacher_batches(tasks, tmp_path / "batches")
    assert code == 0, result
    exported = _load_json(tmp_path / "batches" / "batch_001.json")["rows"][0]
    assert exported == row
    assert "\n" in exported["artifacts"]["rtl_code"]
    assert "\n" in exported["artifacts"]["testbench"]


def test_validator_accepts_valid_conservative_answer(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    answers = tmp_path / "answers.json"
    _write_jsonl(tasks, [_task()])
    answers.write_text(json.dumps({"answers": [_answer()]}), encoding="utf-8")
    result, code = validate_rtl_answer_teacher_batch(tasks, answers, output_md=tmp_path / "report.md", output_json=tmp_path / "report.json", strict=True)
    assert code == 0, result
    assert result["ok"] is True
    assert _load_json(tmp_path / "report.json")["ok"] is True
    assert "Errors" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_validator_rejects_missing_and_duplicate_source_id(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    _write_jsonl(tasks, [_task("task_001")])

    missing = tmp_path / "missing.json"
    missing_answer = _answer()
    missing_answer.pop("source_id")
    missing.write_text(json.dumps([missing_answer]), encoding="utf-8")
    result, code = validate_rtl_answer_teacher_batch(tasks, missing)
    assert code == 1
    assert any("missing source_id" in error for error in result["errors"])

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps([_answer("task_001"), _answer("task_001")]), encoding="utf-8")
    result, code = validate_rtl_answer_teacher_batch(tasks, duplicate)
    assert code == 1
    assert any("duplicates source_id" in error for error in result["errors"])


def test_validator_rejects_unsupported_passed_simulation_claim(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    answers = tmp_path / "answers.json"
    _write_jsonl(tasks, [_task()])
    bad = _answer()
    bad["limitations"] = ["The design passed simulation."]
    answers.write_text(json.dumps([bad]), encoding="utf-8")
    result, code = validate_rtl_answer_teacher_batch(tasks, answers)
    assert code == 1
    assert any("simulation claim" in error for error in result["errors"])


def test_validator_rejects_reference_only_candidate_bug_claim(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    answers = tmp_path / "answers.json"
    _write_jsonl(tasks, [_task()])
    bad = _answer(issue="The candidate DUT has a bug in the q update logic.")
    answers.write_text(json.dumps([bad]), encoding="utf-8")
    result, code = validate_rtl_answer_teacher_batch(tasks, answers)
    assert code == 1
    assert any("candidate DUT bug" in error for error in result["errors"])


def test_validator_allows_prompt_embedded_candidate_bug_discussion(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    answers = tmp_path / "answers.json"
    _write_jsonl(tasks, [_task(candidate=True)])
    answer = _answer(issue="The prompt-embedded candidate DUT has a bug: it inverts d before assigning q.")
    answers.write_text(json.dumps([answer]), encoding="utf-8")
    result, code = validate_rtl_answer_teacher_batch(tasks, answers)
    assert code == 0, result


def test_merge_creates_chat_rows_and_does_not_mutate_tasks(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    answers = tmp_path / "answers.json"
    output = tmp_path / "draft_rows.jsonl"
    _write_jsonl(tasks, [_task("task_001")])
    before = tasks.read_text(encoding="utf-8")
    answers.write_text(json.dumps([_answer("task_001")]), encoding="utf-8")

    result, code = merge_rtl_task_answer_rows(tasks, answers, output, strict=True)
    assert code == 0, result
    assert tasks.read_text(encoding="utf-8") == before
    row = json.loads(output.read_text(encoding="utf-8").strip())
    assert row["review_status"] == "draft"
    assert [message["role"] for message in row["messages"]] == ["system", "user", "assistant"]
    assert row["messages"][1]["content"]["schema_version"] == "rtl_task_v0.1"
    assert row["messages"][2]["content"]["schema_version"] == "rtl_answer_v0.1"
