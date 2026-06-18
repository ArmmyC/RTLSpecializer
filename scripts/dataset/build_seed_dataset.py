#!/usr/bin/env python3
"""Build the deterministic, synthetic v0.1 golden seed dataset."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import write_jsonl

SYSTEM = "You are an RTL review assistant. Be conservative, preserve behavior, distinguish suggestions from tool evidence, and never invent measurements."
REQUIRED_OUTPUT = ["issue_summary", "time_reasoning", "space_reasoning", "safe_optimization", "functional_risk", "verification_plan", "claim_levels"]

EXAMPLES = [
    ("counter", "rtl_bug_review", "find_correctness_bug", "A counter wraps one count earlier than its stated terminal value."),
    ("fsm", "rtl_bug_review", "find_correctness_bug", "The default transition can mask an illegal-state recovery path."),
    ("shift_register", "rtl_bug_review", "find_correctness_bug", "The serial output is indexed from the opposite end of the shift direction."),
    ("handshake", "rtl_bug_review", "find_correctness_bug", "Payload can update while valid is held and ready is low."),
    ("timer", "rtl_bug_review", "find_correctness_bug", "The zero-duration case underflows before asserting done."),
    ("counter", "rtl_area_activity_review", "reduce_switching_activity", "The free-running counter updates while its result is unused."),
    ("mux", "rtl_area_activity_review", "reduce_area", "A duplicated decode cone feeds two equivalent mux branches."),
    ("decoder", "rtl_area_activity_review", "reduce_switching_activity", "The wide decoder evaluates when its output is not consumed."),
    ("register_bank", "rtl_area_activity_review", "reduce_area", "Repeated write-decode expressions may share source-level structure."),
    ("comparator", "rtl_area_activity_review", "reduce_switching_activity", "A wide comparison evaluates outside the qualified transaction window."),
    ("serializer", "rtl_tool_report_explanation", "explain_lint_log", "A lint warning identifies a width truncation at the load boundary."),
    ("fsm", "rtl_tool_report_explanation", "explain_synthesis_report", "A synthesis report lists inferred state and output registers."),
    ("timer", "rtl_tool_report_explanation", "explain_toggle_report", "A toggle report marks the prescaler bits as frequently changing."),
    ("handshake", "unsafe_optimization_rejection", "reject_unsafe_optimization", "Removing the skid register would alter ready/valid timing."),
    ("fsm", "unsafe_optimization_rejection", "reject_unsafe_optimization", "Re-encoding states without equivalence evidence is not justified."),
    ("register_bank", "unsafe_optimization_rejection", "reject_unsafe_optimization", "Sharing a read port could add a cycle of latency."),
    ("timer", "unsafe_optimization_rejection", "reject_unsafe_optimization", "Removing reset from the done register changes observable startup behavior."),
    ("serializer", "rtl_before_after_judgment", "compare_before_after", "The proposed version gates shifting with an enable but needs behavioral comparison."),
    ("decoder", "rtl_before_after_judgment", "compare_before_after", "The proposed case rewrite appears structurally simpler but needs synthesis confirmation."),
    ("shift_register", "rtl_before_after_judgment", "compare_before_after", "The proposed concatenation preserves the apparent shift direction but needs simulation."),
]


def _artifact(task_type: str, index: int) -> dict[str, str | None]:
    fields = {"rtl_code": None, "before_rtl_code": None, "after_rtl_code": None, "testbench": None, "synthesis_report": None, "toggle_report": None, "lint_log": None}
    if task_type == "rtl_before_after_judgment":
        fields["before_rtl_code"] = f"module sample_{index}; // synthetic before RTL\nendmodule"
        fields["after_rtl_code"] = f"module sample_{index}; // synthetic proposed RTL\nendmodule"
    elif task_type == "rtl_tool_report_explanation":
        fields[{10: "lint_log", 11: "synthesis_report", 12: "toggle_report"}[index]] = "Synthetic report excerpt for schema testing; not measured project data."
    else:
        fields["rtl_code"] = f"module sample_{index}; // synthetic illustrative RTL\nendmodule"
    return fields


def make_row(index: int, family: str, task_type: str, goal: str, issue: str) -> dict:
    task = {
        "schema_version": "rtl_task_v0.1", "domain": "digital_rtl", "task_type": task_type, "user_goal": goal,
        "design_context": {"target_domain": "rfid_nfc_digital_ic", "priority": ["correctness", "low_switching_activity", "low_area"], "timing_policy": "timing_is_constraint_not_reward"},
        "artifacts": _artifact(task_type, index),
        "extracted_rtl_summary": {"top_module": f"sample_{index}", "clock_signals": ["clk"], "reset_signals": ["rst_n"], "registered_signals": [], "combinational_blocks": [], "suspected_fsm_signals": [], "suspected_counters": [], "unused_enable_signals": [], "activity_hotspots": []},
        "constraints": {"preserve_top_level_interface": True, "preserve_cycle_level_behavior": True, "preserve_reset_behavior": True, "do_not_claim_power_without_power_report": True, "prefer_minimal_patch": True},
        "assumptions": ["The supplied synthetic artifact is incomplete and tool evidence is unavailable."], "required_output": REQUIRED_OUTPUT,
    }
    activity_task = task_type == "rtl_area_activity_review"
    answer = {
        "schema_version": "rtl_answer_v0.1", "task_type": task_type,
        "issue_summary": [{"issue": issue, "severity": "medium", "evidence": {"signal_names": [], "code_location": {"module": f"sample_{index}", "block": None, "line_range": None}, "reason": "The supplied synthetic artifact motivates review but does not provide tool proof."}}],
        "time_reasoning": {"clock_cycle_behavior": "Preserve all visible cycle boundaries.", "latency_or_state_risk": "Any state, register, latency, or interface change requires explicit comparison.", "reset_behavior_risk": "Preserve reset values and post-reset sequencing."},
        "space_reasoning": {"hardware_resources_involved": [family], "area_risk": "Area impact is unknown until synthesis comparison.", "activity_risk": "Activity impact is unknown until VCD toggle comparison."},
        "safe_optimization": {"recommendation": "Use only a minimal source-level change after confirming the specification.", "patch_style": "minimal", "expected_effect": "The change may address the review concern; quantitative effects remain unmeasured.", "requires_spec_confirmation": task_type in {"unsafe_optimization_rejection", "rtl_before_after_judgment"}},
        "functional_risk": ["A change could alter cycle, reset, state, or interface behavior; compare against the reference."],
        "verification_plan": ["Run lint/compile on the unchanged and proposed RTL", "Run focused simulation and compare cycle-level outputs"],
        "claim_levels": {"correctness": "suggestion_only", "area": "insufficient_evidence", "activity": "suggestion_only" if activity_task else "not_applicable", "power": "insufficient_evidence"},
        "patch": {"provided": False, "patch_type": "none", "diff": None, "notes": "No patch is provided without fuller design context."},
    }
    if activity_task:
        answer["verification_plan"] += ["Synthesis area comparison is required; synthesis evidence is unavailable", "VCD toggle/activity comparison is required; toggle evidence is unavailable"]
    return {
        "id": f"golden_{family}_{index + 1:03d}", "dataset_version": "dataset_v0.1", "split": "unsplit", "source": "handwritten_golden", "license": "project_internal", "design_family": family, "task_family": task_type, "created_by": "human", "review_status": "reviewed",
        "provenance": {"origin": "handwritten", "public_dataset_name": None, "public_dataset_url": None, "source_commit": None, "notes": "Synthetic public-safe schema and tooling example."},
        "tool_checks": {"parse": None, "lint": None, "simulation": None, "equivalence": None, "synthesis": None, "toggle": None, "power": None},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}, {"role": "assistant", "content": answer}],
    }


def main() -> None:
    output = Path(__file__).resolve().parents[2] / "data" / "golden" / "golden_v0.1.jsonl"
    write_jsonl(output, [make_row(index, *example) for index, example in enumerate(EXAMPLES)])


if __name__ == "__main__":
    main()

