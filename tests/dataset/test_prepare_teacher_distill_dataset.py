from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.prepare_teacher_distill_dataset import main as prepare_teacher_distill_main
from scripts.dataset.constants import TEACHER_DISTILL_REVIEW_STATUS, TOOL_CHECKS
from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.teacher_distill import (
    APPROVAL_STATUS,
    DATASET_NAME,
    KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS,
    prepare_teacher_distill_dataset,
)
from scripts.dataset.validation import validate_dataset_file


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    rows, problems = load_jsonl(path)
    assert not problems, [problem.message for problem in problems]
    return [row for _, row in rows]


def _task(source_id: str, *, candidate: bool = False) -> dict:
    before = (
        "module TopModule(input [7:0] a, input [7:0] b, input sel, output out);\n"
        "  assign out = (~sel & a) | (sel & b);\n"
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
        "source_dataset": "public_verilog_eval",
        "license": "see_upstream_verilog_eval",
        "provenance": {
            "public_dataset_name": "VerilogEval",
            "public_dataset_url": "https://github.com/NVlabs/verilog-eval",
            "source_commit": None,
            "notes": "Local staged VerilogEval task fixture.",
        },
        "design_family": "verilog_eval",
        "task_type": "rtl_bug_review",
        "user_goal": "find_correctness_bug",
        "domain": "digital_rtl",
        "prompt": "Review this RTL task and keep the interface unchanged.\n",
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
        "notes": [
            "The supplied RTL is reference RTL.",
            "A prompt-embedded candidate is present." if candidate else "No candidate DUT source is supplied.",
        ],
        "assumptions": [
            "Only supplied task artifacts were inspected.",
            "No tool checks were run.",
        ],
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


def _answer(source_id: str, *, candidate: bool = False) -> dict:
    issue = (
        "The prompt-embedded candidate DUT uses a scalar out declaration for an 8-bit mux result."
        if candidate else
        "No candidate DUT-specific bug can be identified from the supplied reference-only artifacts."
    )
    return {
        "schema_version": "rtl_answer_v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "issue_summary": [{
            "issue": issue,
            "severity": "high" if candidate else "low",
            "evidence": {
                "signal_names": ["out", "a", "b"] if candidate else [],
                "code_location": {"module": "TopModule" if candidate else "RefModule", "block": "text_inspection", "line_range": None},
                "reason": "Only supplied task artifacts were inspected; tool checks are null.",
            },
        }],
        "time_reasoning": {
            "clock_cycle_behavior": "No timed result is claimed.",
            "latency_or_state_risk": "No latency or state change is claimed without verification.",
            "reset_behavior_risk": "No reset behavior was checked.",
        },
        "space_reasoning": {
            "area_risk": "No synthesis report is supplied.",
            "activity_risk": "No toggle or power report is supplied.",
            "hardware_resources_involved": ["mux"] if candidate else ["register"],
        },
        "safe_optimization": {
            "recommendation": "Keep this as a conservative text-inspection answer for pilot fine-tuning only.",
            "patch_style": "explanation_only",
            "expected_effect": "No optimization effect is claimed without evidence.",
            "requires_spec_confirmation": True,
        },
        "functional_risk": [],
        "verification_plan": [
            "Run lint or compile before making syntax or lint claims.",
            "Run simulation or formal checks before making correctness claims.",
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


def _fixture_rows() -> tuple[list[dict], list[dict]]:
    source_ids = [
        KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS[0],
        KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS[1],
        KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS[2],
        "Prob200_ref_a",
        "Prob201_ref_b",
        "Prob202_ref_c",
    ]
    tasks = [
        _task(source_id, candidate=source_id in KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS)
        for source_id in source_ids
    ]
    answers = [
        _answer(source_id, candidate=source_id in KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS)
        for source_id in source_ids
    ]
    return tasks, answers


def _run_prepare(tmp_path, tasks: list[dict], answers: list[dict], *, seed: int = 42):
    tasks_path = tmp_path / "tasks.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    output_dir = tmp_path / "distill"
    _write_jsonl(tasks_path, tasks)
    _write_jsonl(answers_path, answers)
    result, code = prepare_teacher_distill_dataset(
        tasks_path=tasks_path,
        answers_path=answers_path,
        output_dir=output_dir,
        train_size=4,
        validation_size=1,
        test_size=1,
        seed=seed,
        strict=True,
    )
    return result, code, output_dir


def test_valid_task_answer_fixtures_merge_correctly(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    rows = _read_jsonl(output_dir / "all.jsonl")
    assert len(rows) == 6
    assert validate_dataset_file(output_dir / "all.jsonl", strict=True).ok


def test_output_role_order_is_system_user_assistant(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    row = _read_jsonl(output_dir / "all.jsonl")[0]
    assert [message["role"] for message in row["messages"]] == ["system", "user", "assistant"]


def test_user_message_contains_rtl_task_only(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    row = _read_jsonl(output_dir / "all.jsonl")[0]
    user = row["messages"][1]["content"]
    assert user["schema_version"] == "rtl_task_v0.1"
    assert "issue_summary" not in user


def test_assistant_message_contains_rtl_answer_only(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    row = _read_jsonl(output_dir / "all.jsonl")[0]
    assistant = row["messages"][2]["content"]
    assert assistant["schema_version"] == "rtl_answer_v0.1"
    assert "prompt" not in assistant


def test_missing_answer_fails_in_strict_mode(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, _ = _run_prepare(tmp_path, tasks, answers[:-1])
    assert code == 1
    assert any("missing answers" in error for error in result["errors"])


def test_extra_answer_fails_in_strict_mode(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    extra = _answer("Prob999_extra")
    result, code, _ = _run_prepare(tmp_path, tasks, answers + [extra])
    assert code == 1
    assert any("without tasks" in error or "unknown source_id" in error for error in result["errors"])


def test_duplicate_answer_source_id_fails(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, _ = _run_prepare(tmp_path, tasks, answers[:-1] + [answers[0]])
    assert code == 1
    assert any("duplicates source_id" in error for error in result["errors"])


def test_duplicate_task_source_id_fails(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, _ = _run_prepare(tmp_path, tasks + [tasks[0]], answers)
    assert code == 1
    assert any("duplicate source_id" in error for error in result["errors"])


def test_split_counts_are_correct(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    assert result["split_counts"] == {"train": 4, "validation": 1, "test": 1}
    assert len(_read_jsonl(output_dir / "train.jsonl")) == 4
    assert len(_read_jsonl(output_dir / "validation.jsonl")) == 1
    assert len(_read_jsonl(output_dir / "test.jsonl")) == 1


def test_split_is_deterministic_with_same_seed(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result_a, code_a, output_a = _run_prepare(tmp_path / "first", tasks, answers, seed=42)
    result_b, code_b, output_b = _run_prepare(tmp_path / "second", tasks, answers, seed=42)
    assert code_a == code_b == 0, (result_a, result_b)
    assert (output_a / "train.jsonl").read_text(encoding="utf-8") == (output_b / "train.jsonl").read_text(encoding="utf-8")
    assert (output_a / "validation.jsonl").read_text(encoding="utf-8") == (output_b / "validation.jsonl").read_text(encoding="utf-8")
    assert (output_a / "test.jsonl").read_text(encoding="utf-8") == (output_b / "test.jsonl").read_text(encoding="utf-8")


def test_no_source_id_appears_in_more_than_one_split(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    memberships: dict[str, set[str]] = {}
    for split_name in ("train", "validation", "test"):
        for row in _read_jsonl(output_dir / f"{split_name}.jsonl"):
            memberships.setdefault(row["source_id"], set()).add(split_name)
    assert all(len(splits) == 1 for splits in memberships.values())


def test_manifest_includes_sha256_hashes(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    manifest = _read_json(output_dir / "manifest.json")
    assert manifest["dataset_name"] == DATASET_NAME
    assert manifest["input_files"]["tasks"]["sha256"]
    assert manifest["input_files"]["answers"]["sha256"]
    assert manifest["output_files"]["all"]["sha256"]
    assert manifest["output_files"]["train"]["sha256"]
    assert manifest["output_files"]["validation"]["sha256"]
    assert manifest["output_files"]["test"]["sha256"]
    assert manifest["output_files"]["dataset_card"]["sha256"]
    assert manifest["output_files"]["validation_report_json"]["sha256"]
    assert manifest["output_files"]["validation_report_md"]["sha256"]


def test_dataset_card_says_not_golden_and_not_human_reviewed(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    text = (output_dir / "dataset_card.md").read_text(encoding="utf-8").lower()
    assert "not golden" in text
    assert "not human-reviewed" in text


def test_rows_are_marked_teacher_distilled_unreviewed_and_not_approved(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    result, code, output_dir = _run_prepare(tmp_path, tasks, answers)
    assert code == 0, result
    row = _read_jsonl(output_dir / "all.jsonl")[0]
    assert row["review_status"] == TEACHER_DISTILL_REVIEW_STATUS
    assert row["approval_status"] == APPROVAL_STATUS


def test_prepare_teacher_distill_cli_accepts_ratio_splits_and_val_size_alias(tmp_path) -> None:
    tasks_path = tmp_path / "tasks.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    output_dir = tmp_path / "output"
    _write_jsonl(tasks_path, [_task(f"Prob{i:03d}") for i in range(10)])
    _write_jsonl(answers_path, [_answer(f"Prob{i:03d}") for i in range(10)])

    code = prepare_teacher_distill_main([
        "--tasks", str(tasks_path),
        "--answers", str(answers_path),
        "--output-dir", str(output_dir),
        "--train-size", "0.8",
        "--val-size", "0.1",
        "--test-size", "0.1",
        "--json",
    ])

    assert code == 0
    assert len(_read_jsonl(output_dir / "train.jsonl")) == 8
    assert len(_read_jsonl(output_dir / "validation.jsonl")) == 1
    assert len(_read_jsonl(output_dir / "test.jsonl")) == 1


def test_generated_files_are_not_written_into_data_golden(tmp_path) -> None:
    tasks, answers = _fixture_rows()
    tasks_path = tmp_path / "tasks.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    _write_jsonl(tasks_path, tasks)
    _write_jsonl(answers_path, answers)
    output_dir = tmp_path / "data" / "golden" / "pilot"
    result, code = prepare_teacher_distill_dataset(
        tasks_path=tasks_path,
        answers_path=answers_path,
        output_dir=output_dir,
        train_size=4,
        validation_size=1,
        test_size=1,
        seed=42,
        strict=True,
    )
    assert code == 1
    assert any("data/golden" in error for error in result["errors"])
