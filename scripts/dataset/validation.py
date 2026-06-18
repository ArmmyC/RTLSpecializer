"""Layered validation for dataset_v0.1 JSONL files."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
import re
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


_PLACEHOLDER_MARKERS = (
    "// synthetic illustrative rtl",
    "// synthetic before rtl",
    "// synthetic proposed rtl",
)
EVIDENCE_TOOLS = {
    "correctness": ("simulation", "equivalence"),
    "area": ("synthesis",),
    "activity": ("toggle",),
    "power": ("power",),
}
TOOL_ARTIFACTS = {
    "lint": "lint_log",
    "synthesis": "synthesis_report",
    "toggle": "toggle_report",
}
_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)
_SUBSTANTIVE_RE = re.compile(
    r"\b(?:input|output|inout|logic|reg|wire)\b[^;]*(?:[,;)]|$)"
    r"|\bassign\s+[A-Za-z_][A-Za-z0-9_$\[\].]*\s*="
    r"|\balways(?:_ff|_comb|_latch)?\s*(?:@|\()"
    r"|\bcase[xz]?\s*\(|\bif\s*\("
    r"|\b[A-Za-z_][A-Za-z0-9_$\[\].]*\s*(?:<=|=)\s*[^=]",
    re.IGNORECASE | re.MULTILINE,
)


def extract_module_names(text: str) -> list[str]:
    """Extract simple Verilog/SystemVerilog module names without parsing or execution."""
    return _MODULE_RE.findall(text) if isinstance(text, str) else []


def artifact_has_substantive_rtl(text: str) -> bool:
    """Conservatively distinguish useful RTL snippets from empty placeholder modules."""
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False
    without_comments = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", text, flags=re.DOTALL)
    return bool(extract_module_names(without_comments) and _SUBSTANTIVE_RE.search(without_comments))


def is_placeholder_rtl(text: str) -> bool:
    return not artifact_has_substantive_rtl(text)


def tool_check_status(row: dict[str, Any], tool: str) -> str | None:
    checks = row.get("tool_checks")
    if not isinstance(checks, dict):
        return None
    check = checks.get(tool)
    if not isinstance(check, dict) or check.get("status") not in TOOL_STATUSES:
        return None
    return check["status"]


def has_passing_tool_evidence(row: dict[str, Any], tool: str) -> bool:
    return tool_check_status(row, tool) == "pass"


def _relevant_artifact(row: dict[str, Any], tool: str) -> str | None:
    artifact_name = TOOL_ARTIFACTS.get(tool)
    messages = row.get("messages")
    if not artifact_name or not isinstance(messages, list) or len(messages) < 2:
        return None
    user = messages[1]
    task = user.get("content") if isinstance(user, dict) else None
    artifacts = task.get("artifacts") if isinstance(task, dict) else None
    value = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def has_tool_evidence(row: dict[str, Any], tool: str) -> bool:
    status = tool_check_status(row, tool)
    if status not in {"pass", "fail", "unknown"}:
        return False
    check = row["tool_checks"][tool]
    summary = check.get("summary")
    has_summary = isinstance(summary, str) and bool(summary.strip())
    has_artifact = _relevant_artifact(row, tool) is not None
    if not (has_summary or has_artifact):
        return False
    if status in {"fail", "unknown"}:
        messages = row.get("messages")
        task = messages[1].get("content") if isinstance(messages, list) and len(messages) > 1 and isinstance(messages[1], dict) else None
        return isinstance(task, dict) and task.get("task_type") == "rtl_tool_report_explanation" and has_artifact
    return True


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
        if row.get("split") in {"train", "val", "test"} and row.get("review_status") not in {"validated", "reviewed"}:
            error("review_status", "train, val, and test rows must be validated or reviewed")
        if row.get("split") == "unsplit" and row.get("review_status") == "rejected":
            warning("review_status", "rejected unsplit row is not training-ready")
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
        _validate_golden_quality(row, task, answer, error)
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


def _validate_golden_quality(row: dict[str, Any], task: dict[str, Any], answer: dict[str, Any], error: Any) -> None:
    if row.get("source") != "handwritten_golden" or row.get("review_status") != "reviewed":
        return
    row_id = row.get("id", "")
    if not re.fullmatch(r"golden_[a-z0-9_]+_(?:bug|activity|report|reject|compare)_\d{3}", row_id):
        error("id", "reviewed golden row id must use descriptive golden_<family>_<task>_<number> format")
    artifacts = task.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return
    task_type = task.get("task_type")
    rtl_fields = ("before_rtl_code", "after_rtl_code") if task_type == "rtl_before_after_judgment" else ("rtl_code",)
    rtl_values = [(field, artifacts.get(field)) for field in rtl_fields if _is_nonempty(artifacts.get(field))]
    if task_type == "rtl_before_after_judgment":
        for field in rtl_fields:
            if not artifact_has_substantive_rtl(artifacts.get(field)):
                error(f"messages[1].content.artifacts.{field}", "reviewed golden before/after rows require substantive RTL on both sides")
    elif task_type != "rtl_tool_report_explanation":
        if not rtl_values or not artifact_has_substantive_rtl(rtl_values[0][1]):
            error("messages[1].content.artifacts.rtl_code", "reviewed golden rows must contain substantive RTL, not placeholder modules")
    for field, text in rtl_values:
        if not artifact_has_substantive_rtl(text):
            error(f"messages[1].content.artifacts.{field}", "reviewed golden rows must contain substantive RTL, not placeholder modules")
    if not rtl_values:
        return
    module_names = {name.lower() for _, text in rtl_values for name in extract_module_names(text)}
    issues = answer.get("issue_summary", [])
    for index, issue in enumerate(issues if isinstance(issues, list) else []):
        evidence = issue.get("evidence", {}) if isinstance(issue, dict) else {}
        signals = evidence.get("signal_names") if isinstance(evidence, dict) else None
        if not isinstance(signals, list) or not signals:
            error(f"messages[2].content.issue_summary[{index}].evidence.signal_names", "RTL-backed reviewed golden rows require concrete signal names")
        location = evidence.get("code_location", {}) if isinstance(evidence, dict) else {}
        module = location.get("module") if isinstance(location, dict) else None
        if not isinstance(module, str) or module.lower() not in module_names:
            error(f"messages[2].content.issue_summary[{index}].evidence.code_location.module", "must name a module present in the supplied RTL")
        block = location.get("block") if isinstance(location, dict) else None
        if block not in {"always_ff", "always_comb", "always", "assign", "case"}:
            error(f"messages[2].content.issue_summary[{index}].evidence.code_location.block", "must identify a meaningful RTL block")


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
        for domain, tools in EVIDENCE_TOOLS.items():
            if levels.get(domain) == "verified" and not any(has_passing_tool_evidence(row, tool) for tool in tools):
                error(f"messages[2].content.claim_levels.{domain}", f"verified requires a passing {' or '.join(tools)} check")
            if levels.get(domain) == "tool_supported" and not any(has_tool_evidence(row, tool) for tool in tools):
                error(f"messages[2].content.claim_levels.{domain}", f"tool_supported requires meaningful {' or '.join(tools)} evidence")
    if "claim_level" in answer:
        warning("messages[2].content.claim_level", "legacy claim_level is migration-only; use claim_levels")
        if "claim_levels" not in answer:
            error("messages[2].content.claim_levels", "required when legacy claim_level is present")
    for field in ("time_reasoning", "space_reasoning", "patch"):
        if not isinstance(answer.get(field), dict):
            error(f"messages[2].content.{field}", "must be an object")
