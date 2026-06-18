"""Review packet and promotion helpers for public draft dataset rows."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from .constants import ARTIFACT_FIELDS
from .io_utils import load_jsonl, write_jsonl
from .validation import validate_dataset_file


PUBLIC_SOURCES = {
    "public_verilog_eval",
    "public_rtllm",
    "public_rtllm_2",
    "public_rtlfixer",
    "public_openllm_rtl",
    "llm_converted_public",
}
STUB_PHRASES = (
    "Imported public dataset draft row requires review",
    "Treat this as a draft review seed only",
    "No optimization effect is claimed",
    "Imported public artifacts may be incomplete",
)
GENERIC_CLOCK_TEXT = (
    "Cycle-level behavior was not verified during import",
    "must be reviewed against the task intent",
)


@dataclass(frozen=True)
class PromotionConfig:
    target_status: str = "validated"
    allow_stub_answer: bool = False
    strict: bool = False


def rejected_path_for(output: Path) -> Path:
    return output.with_name(output.stem + ".rejected.jsonl")


def _answer_text(answer: dict[str, Any]) -> str:
    return json.dumps(answer, sort_keys=True)


def is_stub_answer(answer: dict[str, Any]) -> bool:
    text = _answer_text(answer)
    if any(phrase in text for phrase in STUB_PHRASES):
        return True
    issues = answer.get("issue_summary")
    if isinstance(issues, list) and issues:
        first = issues[0]
        if isinstance(first, dict) and first.get("severity") == "low" and "imported public" in str(first.get("issue", "")).lower():
            return True
    levels = answer.get("claim_levels")
    all_insufficient = isinstance(levels, dict) and levels and all(value == "insufficient_evidence" for value in levels.values())
    safe = answer.get("safe_optimization")
    patch_style = safe.get("patch_style") if isinstance(safe, dict) else None
    task_type = answer.get("task_type")
    if all_insufficient and task_type != "rtl_tool_report_explanation":
        return True
    if patch_style == "explanation_only" and task_type != "rtl_tool_report_explanation":
        return True
    return False


def _has_rtl_artifact(row: dict[str, Any]) -> bool:
    task = row.get("messages", [{}, {}, {}])[1].get("content", {}) if isinstance(row.get("messages"), list) and len(row["messages"]) > 1 else {}
    artifacts = task.get("artifacts", {}) if isinstance(task, dict) else {}
    return any(isinstance(artifacts.get(name), str) and artifacts.get(name).strip() for name in ("rtl_code", "before_rtl_code", "after_rtl_code"))


def _public_quality_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    task = messages[1].get("content") if len(messages) > 1 and isinstance(messages[1], dict) else {}
    answer = messages[2].get("content") if len(messages) > 2 and isinstance(messages[2], dict) else {}
    if not isinstance(task, dict) or not isinstance(answer, dict):
        return ["messages must contain task and assistant answer objects"]
    issues = answer.get("issue_summary")
    if not isinstance(issues, list) or not issues:
        errors.append("issue_summary must be non-empty")
    else:
        for index, issue in enumerate(issues):
            evidence = issue.get("evidence", {}) if isinstance(issue, dict) else {}
            reason = evidence.get("reason") if isinstance(evidence, dict) else None
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"issue_summary[{index}].evidence.reason must be non-empty")
            signals = evidence.get("signal_names") if isinstance(evidence, dict) else None
            if _has_rtl_artifact(row) and (not isinstance(signals, list) or not any(isinstance(item, str) and item.strip() for item in signals)):
                errors.append(f"issue_summary[{index}].evidence.signal_names must name concrete signals or artifacts")
    time = answer.get("time_reasoning", {})
    clock = time.get("clock_cycle_behavior") if isinstance(time, dict) else None
    if not isinstance(clock, str) or not clock.strip() or any(text in clock for text in GENERIC_CLOCK_TEXT):
        errors.append("time_reasoning.clock_cycle_behavior must be reviewed and non-generic")
    space = answer.get("space_reasoning", {})
    area = space.get("area_risk") if isinstance(space, dict) else None
    activity = space.get("activity_risk") if isinstance(space, dict) else None
    if not isinstance(area, str) or not re.search(r"evidence|synthesis|tool|report|unavailable", area, re.IGNORECASE):
        errors.append("space_reasoning.area_risk must mention evidence limitations or synthesis/tool requirements")
    if not isinstance(activity, str) or not re.search(r"evidence|vcd|toggle|activity|tool|report|unavailable", activity, re.IGNORECASE):
        errors.append("space_reasoning.activity_risk must mention evidence limitations or VCD/toggle requirements")
    plan = answer.get("verification_plan")
    plan_text = " ".join(str(item).lower() for item in plan) if isinstance(plan, list) else ""
    if not ("lint" in plan_text or "compile" in plan_text):
        errors.append("verification_plan must include lint/compile")
    if task.get("task_type") == "rtl_area_activity_review":
        if "synthesis" not in plan_text:
            errors.append("area/activity review verification_plan must mention synthesis")
        if not ("vcd" in plan_text or "toggle" in plan_text):
            errors.append("area/activity review verification_plan must mention VCD/toggle")
    return errors


def public_promotion_errors(row: dict[str, Any], allow_stub_answer: bool = False) -> list[str]:
    errors: list[str] = []
    source = row.get("source")
    license_value = row.get("license")
    provenance = row.get("provenance")
    if source not in PUBLIC_SOURCES:
        errors.append("source must be an allowed public source")
    if not isinstance(license_value, str) or not license_value.strip():
        errors.append("license must be non-empty")
    elif license_value.strip().lower() in {"unknown", "uncertain", "todo"}:
        errors.append("license must not be unknown, uncertain, or todo")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        if not isinstance(provenance.get("public_dataset_name"), str) or not provenance["public_dataset_name"].strip():
            errors.append("provenance.public_dataset_name must be non-empty")
        if not isinstance(provenance.get("notes"), str) or not provenance["notes"].strip():
            errors.append("provenance.notes must be non-empty")
    if row.get("review_status") == "rejected":
        errors.append("rejected rows cannot be promoted")
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    answer = messages[2].get("content") if len(messages) > 2 and isinstance(messages[2], dict) else None
    if isinstance(answer, dict):
        if not allow_stub_answer and is_stub_answer(answer):
            errors.append("stub answer must be edited before promotion")
        errors.extend(_public_quality_errors(row))
    else:
        errors.append("assistant answer must be an object")
    return errors


def _validate_row(row: dict[str, Any], temp: Path) -> list[str]:
    write_jsonl(temp, [row])
    report = validate_dataset_file(temp, strict=True)
    try:
        temp.unlink()
    except FileNotFoundError:
        pass
    return [item.format() for item in report.errors + report.warnings]


def promote_rows(rows: list[dict[str, Any]], output: Path, report_path: Path, config: PromotionConfig) -> tuple[dict[str, Any], int]:
    if config.target_status not in {"validated", "reviewed"}:
        result = _promotion_result(False, len(rows), [], [], output, report_path, [f"invalid target status: {config.target_status}"], [])
        return result, 1
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    temp = output.with_name(output.name + ".validation.tmp.jsonl")
    for row in rows:
        row_id = row.get("id")
        row_errors: list[str] = []
        if not isinstance(row_id, str) or not row_id:
            row_errors.append("id must be a non-empty string")
        elif row_id in seen:
            row_errors.append(f"duplicate row id: {row_id}")
        if not row_errors:
            row_errors.extend(public_promotion_errors(row, config.allow_stub_answer))
        promoted = deepcopy(row)
        promoted["review_status"] = config.target_status
        if not row_errors:
            row_errors.extend(_validate_row(promoted, temp))
        if row_errors:
            rejected.append({"id": row_id, "reason": _reason_for(row_errors), "errors": row_errors, "row": row})
            continue
        seen.add(row_id)
        accepted.append(promoted)
    errors: list[str] = []
    warnings: list[str] = []
    rejected_output = rejected_path_for(output)
    write_jsonl(output, accepted)
    write_jsonl(rejected_output, rejected)
    if accepted:
        full_report = validate_dataset_file(output, strict=True)
        full_errors = [item.format() for item in full_report.errors + full_report.warnings]
        if full_errors:
            errors.extend(full_errors)
    else:
        errors.append("no rows accepted")
    if config.strict and rejected:
        errors.append("strict mode rejects partial promotion with rejected rows")
    result = _promotion_result(not errors, len(rows), accepted, rejected, output, report_path, errors, warnings)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result, 0 if result["ok"] else 1


def _reason_for(errors: list[str]) -> str:
    joined = " ".join(errors).lower()
    if "stub answer" in joined:
        return "stub answer"
    if "license" in joined:
        return "license gate"
    if "provenance" in joined:
        return "provenance gate"
    if "source" in joined:
        return "public source gate"
    if "duplicate" in joined:
        return "duplicate id"
    return "promotion gate failed"


def _promotion_result(
    ok: bool,
    input_rows: int,
    accepted: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    output: Path,
    report_path: Path,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    rejection_reasons = Counter(item.get("reason", "unknown") for item in rejected)
    return {
        "ok": ok,
        "input_rows": input_rows,
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
        "by_source": dict(sorted(Counter(row.get("source") for row in accepted).items())),
        "by_task_type": dict(sorted(Counter(row.get("task_family") for row in accepted).items())),
        "by_design_family": dict(sorted(Counter(row.get("design_family") for row in accepted).items())),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "output": str(output),
        "rejected_output": str(rejected_path_for(output)),
        "report": str(report_path),
        "errors": errors,
        "warnings": warnings,
    }


def load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    loaded, problems = load_jsonl(path)
    return [row for _, row in loaded], [problem.message for problem in problems]


def artifact_items(row: dict[str, Any]) -> list[tuple[str, str]]:
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    task = messages[1].get("content") if len(messages) > 1 and isinstance(messages[1], dict) else {}
    artifacts = task.get("artifacts", {}) if isinstance(task, dict) else {}
    return [
        (name, value) for name in sorted(ARTIFACT_FIELDS)
        if isinstance((value := artifacts.get(name)), str) and value.strip()
    ]
