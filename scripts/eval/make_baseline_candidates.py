#!/usr/bin/env python3
"""Create conservative rule-baseline candidate answers for a dataset split."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.constants import ANSWER_SCHEMA_VERSION
from scripts.dataset.io_utils import write_jsonl
from scripts.eval.evaluator import load_dataset_rows


NO_TOOL_CHECKS_LIMITATION = (
    "All supplied tool_checks entries are null or absent, so parse, lint, simulation, "
    "equivalence, synthesis, power, and toggle checks were not run."
)


def _task_content(row: dict[str, Any]) -> dict[str, Any]:
    return row["messages"][1]["content"]


def _signals(row: dict[str, Any]) -> list[str]:
    summary = _task_content(row).get("extracted_rtl_summary", {})
    signals: list[str] = []
    for key in ("registered_signals", "clock_signals", "reset_signals", "suspected_counters", "suspected_fsm_signals", "activity_hotspots"):
        for item in summary.get(key, []) if isinstance(summary, dict) else []:
            if isinstance(item, str) and item not in signals:
                signals.append(item)
    if signals:
        return signals[:4]
    artifacts = _task_content(row).get("artifacts", {})
    return [key for key, value in artifacts.items() if value][:4] or ["supplied_artifact"]


def _source_id(row: dict[str, Any]) -> str:
    task = _task_content(row)
    for candidate in (task.get("source_id"), row.get("source_id"), row.get("id")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return "unknown_source_id"


def _evidence_used(task: dict[str, Any]) -> list[str]:
    artifacts = task.get("artifacts", {}) if isinstance(task.get("artifacts"), dict) else {}
    evidence: list[str] = []
    for field in ("before_rtl_code", "rtl_code", "after_rtl_code", "testbench", "lint_log", "synthesis_report", "toggle_report"):
        value = artifacts.get(field)
        if isinstance(value, str) and value.strip():
            evidence.append(f"artifacts.{field}")
    if isinstance(task.get("mutation_summary"), str) and task["mutation_summary"].strip():
        evidence.append("mutation_summary")
    if isinstance(task.get("mutated_signal_names"), list) and task["mutated_signal_names"]:
        evidence.append("mutated_signal_names")
    if isinstance(task.get("prompt"), str) and task["prompt"].strip():
        evidence.append("prompt")
    evidence.append("tool_checks")
    deduped: list[str] = []
    for item in evidence:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _limitations(task: dict[str, Any]) -> list[str]:
    checks = task.get("tool_checks")
    limitations: list[str] = []
    if not isinstance(checks, dict) or all(value is None for value in checks.values()):
        limitations.append(NO_TOOL_CHECKS_LIMITATION)
    else:
        limitations.append(
            "This rule baseline does not claim tool outcomes beyond the supplied task artifacts; "
            "inspect tool_checks directly before making correctness or optimization claims."
        )
    limitations.append(
        "This rule baseline is based on text inspection of the supplied prompt and artifacts only."
    )
    return limitations


def make_baseline_answer(row: dict[str, Any]) -> dict[str, Any]:
    task = _task_content(row)
    summary = task.get("extracted_rtl_summary", {})
    module = summary.get("top_module") if isinstance(summary, dict) else None
    signals = _signals(row)
    task_type = task["task_type"]
    verification = [
        "Run lint/compile on the supplied RTL or report artifacts.",
        "Run focused simulation before claiming behavioral correctness.",
        "Run synthesis before making area claims.",
        "Run VCD toggle/activity comparison before making activity claims; evidence is unavailable in this baseline.",
        "Do not make power claims without supplied power evidence.",
    ]
    if task_type == "rtl_area_activity_review":
        verification.append("Compare synthesis area and VCD/toggle activity before accepting any optimization.")
    return {
        "schema_version": ANSWER_SCHEMA_VERSION,
        "source_id": _source_id(row),
        "task_type": task_type,
        "issue_summary": [{
            "issue": "Conservative baseline identifies supplied RTL or report artifacts for review and does not claim a fix or correctness result.",
            "severity": "low",
            "evidence": {
                "signal_names": signals,
                "code_location": {"module": module, "block": "always_ff" if module else "supplied_artifact", "line_range": None},
                "reason": f"The baseline names available signals/artifacts ({', '.join(signals)}) and defers correctness or optimization conclusions to later review.",
            },
        }],
        "time_reasoning": {
            "clock_cycle_behavior": "Baseline requires review of cycle-level behavior before changing state, latency, or interface behavior.",
            "latency_or_state_risk": "Any patch can change latency or state transitions, so this baseline proposes review rather than a fix.",
            "reset_behavior_risk": "Reset behavior must be preserved and checked from the supplied RTL or tests.",
        },
        "space_reasoning": {
            "hardware_resources_involved": signals,
            "area_risk": "Area impact requires synthesis evidence; this baseline has no synthesis result.",
            "activity_risk": "Activity impact requires VCD/toggle evidence; this baseline has no toggle result.",
        },
        "safe_optimization": {
            "recommendation": "Inspect the supplied artifacts and run the verification plan before proposing or accepting changes.",
            "patch_style": "explanation_only",
            "expected_effect": "No correctness, area, activity, or power improvement is claimed.",
            "requires_spec_confirmation": True,
        },
        "functional_risk": ["The artifact may be incomplete or require task-specific interpretation before any safe change is known."],
        "verification_plan": verification,
        "claim_levels": {
            "correctness": "suggestion_only",
            "area": "insufficient_evidence",
            "activity": "insufficient_evidence",
            "power": "insufficient_evidence",
        },
        "evidence_used": _evidence_used(task),
        "limitations": _limitations(task),
        "patch": {"provided": False, "patch_type": "none", "diff": None, "notes": "Rule baseline does not provide patches."},
    }


def make_candidates(dataset: Path, output: Path) -> tuple[dict[str, Any], int]:
    rows, errors = load_dataset_rows(dataset)
    if errors:
        return {"ok": False, "dataset_rows": len(rows), "candidate_rows": 0, "output": str(output), "errors": errors, "warnings": []}, 1
    candidates = [{"id": row["id"], "answer": make_baseline_answer(row), "metadata": {"model": "rule_baseline", "prompt_version": "baseline_v0.1"}} for row in rows]
    write_jsonl(output, candidates)
    return {"ok": True, "dataset_rows": len(rows), "candidate_rows": len(candidates), "output": str(output), "errors": [], "warnings": []}, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = make_candidates(args.dataset, args.output)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["ok"]:
        print("Baseline candidates created.")
        print(f"Dataset rows: {result['dataset_rows']}")
        print(f"Candidate rows: {result['candidate_rows']}")
        print(f"Output: {result['output']}")
    else:
        print("Baseline candidate generation failed.")
        for error in result["errors"]:
            print(f"- {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
