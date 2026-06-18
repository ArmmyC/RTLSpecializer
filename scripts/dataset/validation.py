"""Layered validation for dataset_v0.1 JSONL files."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .claim_safety import find_unsupported_claims
from .constants import (
    ANSWER_SCHEMA_VERSION, ARTIFACT_FIELDS, CLAIM_DOMAINS, CLAIM_LEVELS,
    DATASET_VERSION, PROVENANCE_FIELDS, REQUIRED_OUTPUT, REVIEW_STATUSES,
    SOURCES, SPLITS, TASK_SCHEMA_VERSION, TASK_TYPES, TOOL_CHECKS,
    TOOL_STATUSES, TOP_LEVEL_FIELDS, USER_GOALS,
)
from .io_utils import load_jsonl


@dataclass(frozen=True)
class ValidationMessage:
    file: str
    message: str
    line: int | None = None
    row_id: str | None = None
    field: str | None = None

    def format(self) -> str:
        location = self.file + (f":{self.line}" if self.line is not None else "")
        bits = [location]
        if self.row_id:
            bits.append(f"row_id={self.row_id}")
        if self.field:
            bits.append(f"field={self.field}")
        return " ".join(bits) + ": " + self.message


@dataclass
class ValidationReport:
    ok: bool
    rows: int
    errors: list[ValidationMessage]
    warnings: list[ValidationMessage]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "rows": self.rows,
            "errors": [asdict(item) for item in self.errors],
            "warnings": [asdict(item) for item in self.warnings],
            "summary": self.summary,
        }


def _is_nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def validate_dataset_file(path: Path, strict: bool = False) -> ValidationReport:
    loaded, read_problems = load_jsonl(path)
    errors = [ValidationMessage(str(path), item.message, item.line) for item in read_problems]
    warnings: list[ValidationMessage] = []
    ids: dict[str, int] = {}
    by_task: Counter[str] = Counter()
    families_by_split: dict[str, set[str]] = defaultdict(set)

    for line, row in loaded:
        row_id = row.get("id") if isinstance(row.get("id"), str) else None
        def error(field: str | None, message: str) -> None:
            errors.append(ValidationMessage(str(path), message, line, row_id, field))
        def warning(field: str | None, message: str) -> None:
            warnings.append(ValidationMessage(str(path), message, line, row_id, field))

        for field in sorted(TOP_LEVEL_FIELDS - row.keys()):
            error(field, "missing required top-level field")
        if row_id:
            if row_id in ids:
                error("id", f"duplicate row id; first seen on line {ids[row_id]}")
            else:
                ids[row_id] = line
        elif "id" in row:
            error("id", "must be a non-empty string")
        if row.get("dataset_version") != DATASET_VERSION:
            error("dataset_version", f"must be {DATASET_VERSION!r}")
        for field, allowed in (("split", SPLITS), ("source", SOURCES), ("task_family", TASK_TYPES), ("review_status", REVIEW_STATUSES)):
            if field in row and row.get(field) not in allowed:
                error(field, f"invalid {field} {row.get(field)!r}")
        for field in ("license", "design_family", "created_by"):
            if field in row and not isinstance(row.get(field), str) or field in row and not row.get(field):
                error(field, "must be a non-empty string")

        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            if "provenance" in row: error("provenance", "must be an object")
        else:
            for field in sorted(PROVENANCE_FIELDS - provenance.keys()):
                error(f"provenance.{field}", "missing required field")
            if "notes" in provenance and not isinstance(provenance["notes"], str):
                error("provenance.notes", "must be a string")

        checks = row.get("tool_checks")
        if not isinstance(checks, dict):
            if "tool_checks" in row: error("tool_checks", "must be an object")
        else:
            for name in sorted(TOOL_CHECKS - checks.keys()):
                error(f"tool_checks.{name}", "missing required field")
            for name in TOOL_CHECKS & checks.keys():
                check = checks[name]
                if check is not None and not isinstance(check, dict):
                    error(f"tool_checks.{name}", "must be an object or null")
                elif isinstance(check, dict) and check.get("status") not in TOOL_STATUSES:
                    error(f"tool_checks.{name}.status", f"invalid tool status {check.get('status')!r}")

        messages = row.get("messages")
        if not isinstance(messages, list) or len(messages) != 3:
            if "messages" in row: error("messages", "must contain exactly three messages")
            continue
        expected_roles = ("system", "user", "assistant")
        bad_shape = False
        for index, expected in enumerate(expected_roles):
            message = messages[index]
            if not isinstance(message, dict):
                error(f"messages[{index}]", "must be an object"); bad_shape = True; continue
            if message.get("role") != expected:
                error(f"messages[{index}].role", f"must be {expected!r}")
            if "content" not in message:
                error(f"messages[{index}].content", "missing required field"); bad_shape = True
        if bad_shape:
            continue
        if not isinstance(messages[0].get("content"), str) or not messages[0].get("content"):
            error("messages[0].content", "must be a non-empty string")
        task = messages[1].get("content")
        answer = messages[2].get("content")
        if not isinstance(task, dict):
            error("messages[1].content", "must be an object"); continue
        if not isinstance(answer, dict):
            error("messages[2].content", "must be an object"); continue

        _validate_task(task, row, error)
        _validate_answer(answer, task, row, error, warning)
        for field, message in find_unsupported_claims(row, answer):
            error(field, message)

        task_type = task.get("task_type")
        if task_type in TASK_TYPES:
            by_task[task_type] += 1
        if row.get("split") in {"train", "val", "test"} and row.get("design_family"):
            families_by_split[row["design_family"]].add(row["split"])

    for family, split_set in sorted(families_by_split.items()):
        if len(split_set) > 1:
            errors.append(ValidationMessage(str(path), f"design family appears in multiple splits: {sorted(split_set)}", field="design_family", row_id=family))

    summary = {"by_task_type": dict(sorted(by_task.items()))}
    ok = not errors and (not strict or not warnings)
    return ValidationReport(ok, len(loaded), errors, warnings, summary)


def _validate_task(task: dict[str, Any], row: dict[str, Any], error: Any) -> None:
    if task.get("schema_version") != TASK_SCHEMA_VERSION:
        error("messages[1].content.schema_version", f"must be {TASK_SCHEMA_VERSION!r}")
    if task.get("domain") != "digital_rtl":
        error("messages[1].content.domain", "must be 'digital_rtl'")
    if task.get("task_type") not in TASK_TYPES:
        error("messages[1].content.task_type", f"invalid task type {task.get('task_type')!r}")
    elif task.get("task_type") != row.get("task_family"):
        error("messages[1].content.task_type", "must match row task_family")
    if task.get("user_goal") not in USER_GOALS:
        error("messages[1].content.user_goal", f"invalid user goal {task.get('user_goal')!r}")
    artifacts = task.get("artifacts")
    if not isinstance(artifacts, dict):
        error("messages[1].content.artifacts", "must be an object")
    else:
        for name in sorted(ARTIFACT_FIELDS - artifacts.keys()):
            error(f"messages[1].content.artifacts.{name}", "missing required field")
        if not any(_is_nonempty(artifacts.get(name)) for name in ARTIFACT_FIELDS):
            error("messages[1].content.artifacts", "at least one artifact must be non-null and non-empty")
    constraints = task.get("constraints")
    if not isinstance(constraints, dict):
        error("messages[1].content.constraints", "must be an object")
    else:
        constraint_fields = {
            "preserve_top_level_interface", "preserve_cycle_level_behavior",
            "preserve_reset_behavior", "do_not_claim_power_without_power_report",
            "prefer_minimal_patch",
        }
        for name in sorted(constraint_fields - constraints.keys()):
            error(f"messages[1].content.constraints.{name}", "missing required field")
        if constraints.get("do_not_claim_power_without_power_report") is not True:
            error("messages[1].content.constraints.do_not_claim_power_without_power_report", "must be true")
        if task.get("task_type") != "unsafe_optimization_rejection" and constraints.get("preserve_cycle_level_behavior") is not True:
            error("messages[1].content.constraints.preserve_cycle_level_behavior", "must be true")
    required = task.get("required_output")
    if not isinstance(required, list) or not REQUIRED_OUTPUT.issubset(set(required)):
        error("messages[1].content.required_output", f"must include {sorted(REQUIRED_OUTPUT)}")
    context = task.get("design_context")
    if not isinstance(context, dict):
        error("messages[1].content.design_context", "must be an object")
    else:
        for name in ("target_domain", "priority", "timing_policy"):
            if name not in context:
                error(f"messages[1].content.design_context.{name}", "missing required field")
    summary = task.get("extracted_rtl_summary")
    summary_fields = {
        "top_module", "clock_signals", "reset_signals", "registered_signals",
        "combinational_blocks", "suspected_fsm_signals", "suspected_counters",
        "unused_enable_signals", "activity_hotspots",
    }
    if not isinstance(summary, dict):
        error("messages[1].content.extracted_rtl_summary", "must be an object")
    else:
        for name in sorted(summary_fields - summary.keys()):
            error(f"messages[1].content.extracted_rtl_summary.{name}", "missing required field")
    if not isinstance(task.get("assumptions"), list):
        error("messages[1].content.assumptions", "must be a list")


def _validate_answer(answer: dict[str, Any], task: dict[str, Any], row: dict[str, Any], error: Any, warning: Any) -> None:
    required_fields = {
        "schema_version", "task_type", "issue_summary", "time_reasoning", "space_reasoning",
        "safe_optimization", "functional_risk", "verification_plan", "claim_levels", "patch",
    }
    for field in sorted(required_fields - answer.keys()):
        error(f"messages[2].content.{field}", "missing required field")
    if answer.get("schema_version") != ANSWER_SCHEMA_VERSION:
        error("messages[2].content.schema_version", f"must be {ANSWER_SCHEMA_VERSION!r}")
    if answer.get("task_type") != task.get("task_type"):
        error("messages[2].content.task_type", "must match user task_type")
    issues = answer.get("issue_summary")
    if not isinstance(issues, list) or not issues:
        error("messages[2].content.issue_summary", "must be a non-empty list")
    else:
        for index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                error(f"messages[2].content.issue_summary[{index}]", "must be an object")
            elif issue.get("severity") not in {"low", "medium", "high"}:
                error(f"messages[2].content.issue_summary[{index}].severity", f"invalid severity {issue.get('severity')!r}")
            else:
                if not isinstance(issue.get("issue"), str) or not issue.get("issue"):
                    error(f"messages[2].content.issue_summary[{index}].issue", "must be a non-empty string")
                evidence = issue.get("evidence")
                if not isinstance(evidence, dict):
                    error(f"messages[2].content.issue_summary[{index}].evidence", "must be an object")
                else:
                    for name in ("signal_names", "code_location", "reason"):
                        if name not in evidence:
                            error(f"messages[2].content.issue_summary[{index}].evidence.{name}", "missing required field")
                    location = evidence.get("code_location")
                    if not isinstance(location, dict):
                        error(f"messages[2].content.issue_summary[{index}].evidence.code_location", "must be an object")
                    else:
                        for name in ("module", "block"):
                            if name not in location:
                                error(f"messages[2].content.issue_summary[{index}].evidence.code_location.{name}", "missing required field")
                        if "line_range" not in location:
                            warning(f"messages[2].content.issue_summary[{index}].evidence.code_location.line_range", "is missing")
    safe = answer.get("safe_optimization")
    risks = answer.get("functional_risk")
    if not isinstance(safe, dict):
        error("messages[2].content.safe_optimization", "must be an object")
    elif safe.get("patch_style") != "explanation_only" and (not isinstance(risks, list) or not risks):
        error("messages[2].content.functional_risk", "must be non-empty when patch_style is not explanation_only")
    elif not isinstance(risks, list):
        error("messages[2].content.functional_risk", "must be a list")
    plan = answer.get("verification_plan")
    if not isinstance(plan, list):
        error("messages[2].content.verification_plan", "must be a list")
    else:
        plan_text = " ".join(str(item).lower() for item in plan)
        if not ("lint" in plan_text or "compile" in plan_text):
            error("messages[2].content.verification_plan", "must include lint/compile")
        if task.get("task_type") == "rtl_area_activity_review":
            if not ("synthesis" in plan_text and ("area" in plan_text or "unavailable" in plan_text)):
                error("messages[2].content.verification_plan", "area/activity review must include synthesis area comparison or note unavailable evidence")
            if not (("vcd" in plan_text or "toggle" in plan_text or "activity" in plan_text) and ("comparison" in plan_text or "unavailable" in plan_text)):
                error("messages[2].content.verification_plan", "activity review must include VCD toggle/activity comparison or note unavailable evidence")
    levels = answer.get("claim_levels")
    if not isinstance(levels, dict):
        error("messages[2].content.claim_levels", "must be an object")
    else:
        for domain in sorted(CLAIM_DOMAINS):
            if levels.get(domain) not in CLAIM_LEVELS:
                error(f"messages[2].content.claim_levels.{domain}", f"invalid claim level {levels.get(domain)!r}")
        evidence_tools = {
            "correctness": ("simulation", "equivalence"),
            "area": ("synthesis",), "activity": ("toggle",), "power": ("power",),
        }
        row_checks = row.get("tool_checks", {}) if isinstance(row.get("tool_checks"), dict) else {}
        for domain, tools in evidence_tools.items():
            if levels.get(domain) in {"tool_supported", "verified"} and not any(row_checks.get(tool) for tool in tools):
                error(f"messages[2].content.claim_levels.{domain}", f"{levels[domain]} requires {' or '.join(tools)} evidence")
    if "claim_level" in answer:
        warning("messages[2].content.claim_level", "legacy claim_level is migration-only; use claim_levels")
        if "claim_levels" not in answer:
            error("messages[2].content.claim_levels", "required when legacy claim_level is present")
    for field in ("time_reasoning", "space_reasoning", "patch"):
        if not isinstance(answer.get(field), dict):
            error(f"messages[2].content.{field}", "must be an object")
