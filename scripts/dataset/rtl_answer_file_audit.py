"""Audit and repair standalone rtl_answer.v0.1 teacher-answer files.

The helpers in this module treat every row as untrusted data. They do not run
RTL, call models, or infer semantic correctness beyond conservative text checks.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import fnmatch
import json
from pathlib import Path
import re
import shutil
from typing import Any

from scripts.dataset.io_utils import load_jsonl, write_jsonl


CANONICAL_ANSWER_SCHEMA_VERSION = "rtl_answer.v0.1"
ANSWER_SCHEMA_ALIASES = {"rtl_answer_v0.1"}
TASK_SCHEMA_VERSIONS = {"rtl_task_v0.1", "rtl_task.v0.1"}
DEFAULT_GLOB = "*rtl_answer*v0_1*.json*"
REQUIRED_FIELDS = {
    "source_id",
    "task_type",
    "issue_summary",
    "time_reasoning",
    "space_reasoning",
    "safe_optimization",
    "functional_risk",
    "verification_plan",
    "claim_levels",
    "evidence_used",
    "limitations",
}
VALID_SEVERITIES = {"low", "medium", "high", "critical"}
CONSERVATIVE_CORRECTNESS_LEVELS = {"suggestion_only", "text_inspection", "insufficient_evidence"}
STRONG_CLAIM_LEVELS = {"verified", "passed", "correct", "simulation_passed", "lint_clean", "tool_supported"}
NON_CORRECTNESS_DOMAINS = {"area", "activity", "power"}
LIST_FIELDS = {"evidence_used", "functional_risk", "verification_plan", "limitations"}
GENERIC_SIGNAL_LABELS = {
    "edge-triggered register",
    "vector datapath",
    "mux/output assignment",
    "reset branch",
    "state/counter register",
    "parity generation logic",
}
SYNTHETIC_EVIDENCE_PREFERENCE = [
    "artifacts.before_rtl_code",
    "artifacts.rtl_code",
    "mutation_summary",
    "mutated_signal_names",
    "prompt",
    "tool_checks",
]
NO_TOOL_CHECKS_LIMITATION = (
    "tool_checks are null or absent, so parse, lint, simulation, equivalence, "
    "synthesis, power, and toggle checks were not run."
)
SKIP_NAME_MARKERS = (
    "manifest",
    "dataset_card",
    "validation",
    "report",
    "summary",
    "scores",
)
RTL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\[[^\]]+\])?$")
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_$]*\b")
RTL_KEYWORDS = {
    "always",
    "and",
    "assign",
    "begin",
    "case",
    "default",
    "else",
    "end",
    "endcase",
    "endmodule",
    "for",
    "generate",
    "if",
    "inout",
    "input",
    "logic",
    "module",
    "negedge",
    "or",
    "output",
    "parameter",
    "posedge",
    "reg",
    "wire",
}
UNSUPPORTED_PHRASES: list[tuple[str, re.Pattern[str], str]] = [
    ("simulation", re.compile(r"\bpassed\s+simulation\b|\bsimulation\s+passed\b|\bverified\s+by\s+simulation\b", re.IGNORECASE), "simulation"),
    ("lint", re.compile(r"\bpassed\s+lint\b|\blint\s+passed\b|\blint\s+clean\b|\bno\s+lint\s+errors\b", re.IGNORECASE), "lint"),
    ("synthesis", re.compile(r"\bsynthesized\s+successfully\b|\bsynthesis\s+passed\b|\bsynthesis\s+shows\b", re.IGNORECASE), "synthesis"),
    ("area", re.compile(r"\barea\s+(?:improved|reduced|decreased)\b", re.IGNORECASE), "synthesis"),
    ("power", re.compile(r"\bpower\s+(?:improved|reduced|decreased)\b", re.IGNORECASE), "power"),
    ("timing", re.compile(r"\btiming\s+(?:met|passed|improved)\b|\bmet\s+timing\b", re.IGNORECASE), "synthesis"),
    ("formal", re.compile(r"\bequivalent\s+by\s+formal\b|\bequivalence\s+passed\b|\bformal\s+(?:passed|proved)\b", re.IGNORECASE), "equivalence"),
]
MUTATION_HINTS: dict[str, tuple[str, ...]] = {
    "wrong_reset_polarity": ("reset", "polarity", "active"),
    "wrong_mux_select_polarity": ("mux", "select", "polarity", "ternary"),
    "incomplete_comb_assignment": ("incomplete", "comb", "assignment", "latch", "else"),
    "off_by_one_counter_limit": ("off-by-one", "counter", "limit", "comparison", "<", "<=", ">", ">="),
    "shift_direction_flip": ("shift", "<<", ">>", "direction"),
    "blocking_nonblocking_swap_in_clocked_block": ("blocking", "nonblocking", "<=", "clocked"),
    "width_truncation_output": ("width", "truncat", "bit 0", "vector"),
    "wrong_fsm_reset_state": ("fsm", "state", "reset"),
}


class AuditIssue(dict):
    """Dict subclass used only to make issue construction readable."""


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    file: Path,
    source_id: str | None = None,
    field: str | None = None,
    row_index: int | None = None,
) -> AuditIssue:
    return AuditIssue({
        "severity": severity,
        "code": code,
        "message": message,
        "file": str(file),
        "source_id": source_id,
        "field": field,
        "row_index": row_index,
    })


def _change(
    file: Path,
    source_id: str | None,
    field: str,
    old_value: Any,
    new_value: Any,
    reason: str,
    fix_type: str = "automatic",
) -> dict[str, Any]:
    return {
        "file": str(file),
        "source_id": source_id,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "reason": reason,
        "fix_type": fix_type,
    }


def _is_answer_object(value: Any) -> bool:
    return isinstance(value, dict) and value.get("schema_version") in ({CANONICAL_ANSWER_SCHEMA_VERSION} | ANSWER_SCHEMA_ALIASES)


def _contains_task_object(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("schema_version") in TASK_SCHEMA_VERSIONS:
            return True
        task_keys = {"prompt", "artifacts", "constraints", "required_output"}
        if len(task_keys & value.keys()) >= 3:
            return True
        return any(_contains_task_object(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_task_object(item) for item in value)
    return False


def _is_chat_row(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    messages = value.get("messages")
    return isinstance(messages, list) and any(isinstance(item, dict) and item.get("role") == "assistant" for item in messages)


def _load_answer_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not path.exists():
        return None, [f"file not found: {path}"]
    if path.is_symlink():
        return None, [f"refusing to read symlinked file: {path}"]

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("answers"), list):
            rows = payload["answers"]
            kind = "json_wrapper"
        elif isinstance(payload, list):
            rows = payload
            kind = "json_array"
        elif _is_answer_object(payload):
            rows = [payload]
            kind = "json_single"
        elif _is_chat_row(payload):
            return {"path": path, "kind": "skipped_chat_row", "payload": payload, "rows": []}, []
        else:
            return None, [f"{path} does not contain standalone rtl_answer rows"]
        normalized = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                errors.append(f"{path}: answer {index} must be a JSON object")
            else:
                normalized.append(row)
        return {"path": path, "kind": kind, "payload": payload, "rows": normalized}, errors
    except json.JSONDecodeError:
        loaded, problems = load_jsonl(path)
        if problems:
            return None, [f"{path}:{problem.line or ''}: {problem.message}" for problem in problems]
        rows = [row for _, row in loaded]
        if rows and all(_is_chat_row(row) for row in rows):
            return {"path": path, "kind": "skipped_chat_jsonl", "payload": None, "rows": []}, []
        answer_rows = [row for row in rows if _is_answer_object(row)]
        if not answer_rows:
            return None, [f"{path} does not contain standalone rtl_answer JSONL rows"]
        if len(answer_rows) != len(rows):
            errors.append(f"{path}: JSONL file mixes answer rows with non-answer rows")
        return {"path": path, "kind": "jsonl", "payload": None, "rows": answer_rows}, errors
    except (OSError, UnicodeError) as exc:
        return None, [f"could not read {path}: {exc}"]


def _write_answer_file(path: Path, loaded: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kind = loaded["kind"]
    if kind == "json_wrapper":
        payload = deepcopy(loaded["payload"])
        payload["answers"] = rows
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif kind == "json_array":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif kind == "json_single":
        payload = rows[0] if rows else {}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif kind == "jsonl":
        write_jsonl(path, rows)
    else:
        raise ValueError(f"cannot write skipped answer file kind: {kind}")


def _skip_by_name(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in SKIP_NAME_MARKERS)


def discover_answer_files(
    inputs: list[Path] | None = None,
    input_dirs: list[Path] | None = None,
    glob_pattern: str = DEFAULT_GLOB,
    exclude_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[tuple[Path, Path | None]] = []
    errors: list[str] = []
    for path in inputs or []:
        candidates.append((path, path.parent.resolve()))
    for directory in input_dirs or []:
        if not directory.exists():
            errors.append(f"input dir not found: {directory}")
            continue
        if not directory.is_dir():
            errors.append(f"input dir is not a directory: {directory}")
            continue
        for path in sorted(directory.rglob(glob_pattern)):
            if path.is_file():
                candidates.append((path, directory.resolve()))

    unique: dict[Path, Path | None] = {}
    resolved_exclude = exclude_dir.resolve() if exclude_dir else None
    for path, base in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved_exclude is not None:
            try:
                resolved.relative_to(resolved_exclude)
                continue
            except ValueError:
                pass
        if _skip_by_name(path):
            continue
        unique[resolved] = base

    loaded_files: list[dict[str, Any]] = []
    for resolved, base in sorted(unique.items(), key=lambda item: str(item[0])):
        loaded, load_errors = _load_answer_file(resolved)
        if loaded is None:
            if load_errors and any("does not contain standalone rtl_answer" not in item for item in load_errors):
                errors.extend(load_errors)
            continue
        loaded["base_dir"] = base
        loaded_files.append(loaded)
        errors.extend(load_errors)
    return loaded_files, errors


def _load_tasks(tasks_path: Path | None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if tasks_path is None:
        return {}, []
    rows, problems = load_jsonl(tasks_path)
    errors = [f"{tasks_path}:{problem.line or ''}: {problem.message}" for problem in problems]
    tasks: dict[str, dict[str, Any]] = {}
    for line, row in rows:
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"{tasks_path}:{line}: task row missing source_id")
            continue
        if source_id in tasks:
            errors.append(f"{tasks_path}:{line}: duplicate task source_id {source_id}")
            continue
        tasks[source_id] = row
    return tasks, errors


def _task_has_tool_evidence(task: dict[str, Any] | None, tool: str) -> bool:
    if not isinstance(task, dict):
        return False
    checks = task.get("tool_checks")
    if isinstance(checks, dict):
        value = checks.get(tool)
        if isinstance(value, dict) and value.get("status") not in {None, "not_run"}:
            return True
        if value not in {None, False} and not isinstance(value, dict):
            return True
    artifacts = task.get("artifacts")
    artifact_by_tool = {"synthesis": "synthesis_report", "toggle": "toggle_report", "power": "power_report", "lint": "lint_log"}
    artifact_name = artifact_by_tool.get(tool)
    return bool(isinstance(artifacts, dict) and artifact_name and isinstance(artifacts.get(artifact_name), str) and artifacts[artifact_name].strip())


def _domain_has_evidence(task: dict[str, Any] | None, domain: str) -> bool:
    tools = {
        "correctness": ("simulation", "equivalence"),
        "area": ("synthesis",),
        "activity": ("toggle",),
        "power": ("power",),
    }.get(domain, ())
    return any(_task_has_tool_evidence(task, tool) for tool in tools)


def _all_tool_checks_null_or_absent(task: dict[str, Any] | None) -> bool:
    if not isinstance(task, dict):
        return True
    checks = task.get("tool_checks")
    return not isinstance(checks, dict) or all(value is None for value in checks.values())


def _answer_text(answer: dict[str, Any]) -> str:
    return json.dumps(answer, ensure_ascii=False, sort_keys=True)


def _limitations_text(answer: dict[str, Any]) -> str:
    limitations = answer.get("limitations")
    if not isinstance(limitations, list):
        return ""
    return "\n".join(str(item) for item in limitations)


def _mentions_tool_checks_not_run(answer: dict[str, Any]) -> bool:
    text = _limitations_text(answer).lower()
    return "tool_checks" in text and ("not run" in text or "null" in text or "absent" in text)


def _is_synthetic_candidate(answer: dict[str, Any], task: dict[str, Any] | None = None) -> bool:
    source_id = answer.get("source_id")
    if isinstance(source_id, str) and "_synthetic_" in source_id:
        return True
    if isinstance(task, dict):
        artifacts = task.get("artifacts")
        context = task.get("design_context")
        return (
            task.get("synthetic_bug") is True
            or (isinstance(artifacts, dict) and isinstance(artifacts.get("before_rtl_code"), str) and bool(artifacts["before_rtl_code"].strip()))
            or (isinstance(context, dict) and context.get("prompt_embedded_candidate_rtl") is True)
        )
    evidence = answer.get("evidence_used")
    return isinstance(evidence, list) and any(str(item) in {"artifacts.before_rtl_code", "mutation_summary"} for item in evidence)


def _task_identifiers(task: dict[str, Any] | None) -> set[str]:
    if not isinstance(task, dict):
        return set()
    texts: list[str] = []
    artifacts = task.get("artifacts")
    if isinstance(artifacts, dict):
        for field in ("rtl_code", "before_rtl_code", "after_rtl_code"):
            value = artifacts.get(field)
            if isinstance(value, str):
                texts.append(value)
    names = {match.group(0) for text in texts for match in IDENTIFIER_RE.finditer(text)}
    names.update(str(item) for item in task.get("mutated_signal_names", []) if isinstance(item, str))
    context = task.get("design_context")
    if isinstance(context, dict):
        for field in ("target_module_name", "rtl_module_name"):
            if isinstance(context.get(field), str):
                names.add(context[field])
    return {name for name in names if name.lower() not in RTL_KEYWORDS}


def _is_generic_signal_label(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in GENERIC_SIGNAL_LABELS or not RTL_IDENTIFIER_RE.fullmatch(value.strip())


def _bug_type_from_source_id(source_id: str) -> str | None:
    marker = "_synthetic_"
    if marker not in source_id:
        return None
    return source_id.split(marker, 1)[1]


def _issue_text(answer: dict[str, Any]) -> str:
    parts: list[str] = []
    issues = answer.get("issue_summary")
    if isinstance(issues, list):
        for issue in issues:
            if isinstance(issue, dict):
                parts.append(str(issue.get("issue", "")))
                evidence = issue.get("evidence")
                if isinstance(evidence, dict):
                    parts.append(str(evidence.get("reason", "")))
    return "\n".join(parts).lower()


def _dedupe_list(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def validate_answer_row(
    answer: dict[str, Any],
    file: Path,
    row_index: int,
    task: dict[str, Any] | None = None,
) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    source_id = answer.get("source_id") if isinstance(answer.get("source_id"), str) else None
    if answer.get("schema_version") != CANONICAL_ANSWER_SCHEMA_VERSION:
        if answer.get("schema_version") in ANSWER_SCHEMA_ALIASES:
            issues.append(_issue("error", "schema_alias", "schema_version uses repairable rtl_answer_v0.1 alias", file=file, source_id=source_id, field="schema_version", row_index=row_index))
        else:
            issues.append(_issue("error", "schema_version", f"schema_version must be {CANONICAL_ANSWER_SCHEMA_VERSION!r}", file=file, source_id=source_id, field="schema_version", row_index=row_index))
    missing = sorted(REQUIRED_FIELDS - answer.keys())
    if missing:
        issues.append(_issue("error", "missing_required_fields", f"missing required fields: {', '.join(missing)}", file=file, source_id=source_id, row_index=row_index))
    if not source_id:
        issues.append(_issue("error", "missing_source_id", "source_id must be a non-empty string", file=file, field="source_id", row_index=row_index))
    if _contains_task_object(answer):
        issues.append(_issue("manual_review", "answer_contains_task_copy", "answer appears to copy a full rtl_task object", file=file, source_id=source_id, field="answer", row_index=row_index))

    issues_field = answer.get("issue_summary")
    if not isinstance(issues_field, list):
        issues.append(_issue("error", "issue_summary_type", "issue_summary must be a list", file=file, source_id=source_id, field="issue_summary", row_index=row_index))
    elif _is_synthetic_candidate(answer, task) and not issues_field:
        issues.append(_issue("error", "empty_candidate_issue_summary", "candidate-bug rows must have non-empty issue_summary", file=file, source_id=source_id, field="issue_summary", row_index=row_index))
    elif isinstance(issues_field, list):
        known_identifiers = _task_identifiers(task)
        for issue_index, issue in enumerate(issues_field):
            if not isinstance(issue, dict):
                issues.append(_issue("error", "issue_summary_item_type", "issue_summary items must be objects", file=file, source_id=source_id, field=f"issue_summary[{issue_index}]", row_index=row_index))
                continue
            severity = issue.get("severity")
            if severity not in VALID_SEVERITIES:
                issues.append(_issue("error", "invalid_severity", f"severity must be one of {sorted(VALID_SEVERITIES)}", file=file, source_id=source_id, field=f"issue_summary[{issue_index}].severity", row_index=row_index))
            evidence = issue.get("evidence")
            signal_names = evidence.get("signal_names") if isinstance(evidence, dict) else None
            if isinstance(signal_names, list):
                for name in signal_names:
                    if not isinstance(name, str) or _is_generic_signal_label(name):
                        issues.append(_issue("error", "generic_signal_name", f"signal_names contains generic or non-RTL label {name!r}", file=file, source_id=source_id, field=f"issue_summary[{issue_index}].evidence.signal_names", row_index=row_index))
                    elif known_identifiers and name.split("[", 1)[0] not in known_identifiers:
                        issues.append(_issue("manual_review", "unknown_signal_name", f"signal name {name!r} was not found in matching task RTL identifiers", file=file, source_id=source_id, field=f"issue_summary[{issue_index}].evidence.signal_names", row_index=row_index))
            line_range = None
            if isinstance(evidence, dict) and isinstance(evidence.get("code_location"), dict):
                line_range = evidence["code_location"].get("line_range")
            if line_range is None or line_range == "":
                issues.append(_issue("manual_review", "missing_line_range", "line_range is missing and cannot be safely inferred", file=file, source_id=source_id, field=f"issue_summary[{issue_index}].evidence.code_location.line_range", row_index=row_index))

    levels = answer.get("claim_levels")
    if not isinstance(levels, dict):
        issues.append(_issue("error", "claim_levels_type", "claim_levels must be an object", file=file, source_id=source_id, field="claim_levels", row_index=row_index))
    else:
        correctness = levels.get("correctness")
        if not _domain_has_evidence(task, "correctness") and correctness not in CONSERVATIVE_CORRECTNESS_LEVELS:
            issues.append(_issue("error", "strong_correctness_claim", "claim_levels.correctness must stay conservative without simulation/equivalence evidence", file=file, source_id=source_id, field="claim_levels.correctness", row_index=row_index))
        if correctness in STRONG_CLAIM_LEVELS and not _domain_has_evidence(task, "correctness"):
            issues.append(_issue("error", "unsupported_correctness_claim_level", f"unsupported correctness claim level {correctness!r}", file=file, source_id=source_id, field="claim_levels.correctness", row_index=row_index))
        for domain in sorted(NON_CORRECTNESS_DOMAINS):
            if not _domain_has_evidence(task, domain) and levels.get(domain) != "insufficient_evidence":
                issues.append(_issue("error", f"unsupported_{domain}_claim_level", f"claim_levels.{domain} must be insufficient_evidence without supplied evidence", file=file, source_id=source_id, field=f"claim_levels.{domain}", row_index=row_index))

    text = _answer_text(answer)
    for code, pattern, evidence_tool in UNSUPPORTED_PHRASES:
        if pattern.search(text) and not _task_has_tool_evidence(task, evidence_tool):
            issues.append(_issue("error", f"unsupported_{code}_wording", f"unsupported {code} wording without matching supplied evidence", file=file, source_id=source_id, field="answer", row_index=row_index))
    if re.search(r"\bverified\s+by\s+text\s+inspection\b", text, re.IGNORECASE):
        issues.append(_issue("error", "verified_by_text_inspection_wording", "use reviewed by text inspection instead of verified by text inspection", file=file, source_id=source_id, field="answer", row_index=row_index))

    evidence_used = answer.get("evidence_used")
    if not isinstance(evidence_used, list):
        issues.append(_issue("error", "evidence_used_type", "evidence_used must be a list", file=file, source_id=source_id, field="evidence_used", row_index=row_index))
    else:
        if _mentions_tool_checks_not_run(answer) and "tool_checks" not in [str(item) for item in evidence_used]:
            issues.append(_issue("error", "missing_tool_checks_evidence", "limitations mention null/not-run tool_checks but evidence_used omits tool_checks", file=file, source_id=source_id, field="evidence_used", row_index=row_index))
        if _is_synthetic_candidate(answer, task):
            missing_preferred = [item for item in SYNTHETIC_EVIDENCE_PREFERENCE if item not in [str(value) for value in evidence_used]]
            if missing_preferred:
                issues.append(_issue("warning", "synthetic_evidence_incomplete", f"synthetic answer evidence_used is missing preferred entries: {', '.join(missing_preferred)}", file=file, source_id=source_id, field="evidence_used", row_index=row_index))

    if _all_tool_checks_null_or_absent(task) and not _mentions_tool_checks_not_run(answer):
        issues.append(_issue("error", "missing_tool_checks_limitation", "limitations must say tool checks were not run when tool_checks are null or absent", file=file, source_id=source_id, field="limitations", row_index=row_index))

    if source_id:
        bug_type = _bug_type_from_source_id(source_id)
        if bug_type in MUTATION_HINTS:
            issue_text = _issue_text(answer)
            if not any(hint in issue_text for hint in MUTATION_HINTS[bug_type]):
                issues.append(_issue("manual_review", "suspicious_mutation_label_mismatch", f"source_id mutation type {bug_type!r} is not clearly reflected in issue text", file=file, source_id=source_id, field="issue_summary", row_index=row_index))
    if _is_synthetic_candidate(answer, task) is False and re.search(r"\b(candidate|dut)\b.{0,80}\b(bug|incorrect|wrong|broken|defect)\b", _issue_text(answer)):
        issues.append(_issue("manual_review", "bug_claim_without_candidate", "answer discusses a candidate bug but no candidate RTL or mutation metadata is evident", file=file, source_id=source_id, field="issue_summary", row_index=row_index))
    return issues


def validate_answer_files(
    inputs: list[Path] | None = None,
    input_dirs: list[Path] | None = None,
    glob_pattern: str = DEFAULT_GLOB,
    tasks_path: Path | None = None,
    output_md: Path | None = None,
    output_json: Path | None = None,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    files, discovery_errors = discover_answer_files(inputs, input_dirs, glob_pattern)
    tasks, task_errors = _load_tasks(tasks_path)
    issues: list[AuditIssue] = []
    for error in discovery_errors + task_errors:
        issues.append(_issue("error", "load_error", error, file=Path("<discovery>")))

    seen_sources: dict[str, str] = {}
    answered_sources: set[str] = set()
    answer_count = 0
    file_summaries: list[dict[str, Any]] = []
    for loaded in files:
        path = loaded["path"]
        rows = loaded["rows"]
        file_issue_start = len(issues)
        for index, row in enumerate(rows, 1):
            answer_count += 1
            source_id = row.get("source_id") if isinstance(row.get("source_id"), str) else None
            task = tasks.get(source_id) if source_id else None
            if source_id:
                answered_sources.add(source_id)
                if source_id in seen_sources:
                    issues.append(_issue("error", "duplicate_source_id", f"source_id also appears in {seen_sources[source_id]}", file=path, source_id=source_id, field="source_id", row_index=index))
                else:
                    seen_sources[source_id] = str(path)
                if tasks_path is not None and task is None:
                    issues.append(_issue("error", "answer_missing_matching_task", "answer source_id has no matching task row", file=path, source_id=source_id, field="source_id", row_index=index))
            issues.extend(validate_answer_row(row, path, index, task))
        file_issues = issues[file_issue_start:]
        file_summaries.append({
            "path": str(path),
            "kind": loaded["kind"],
            "answers": len(rows),
            "issues": len(file_issues),
            "errors": sum(1 for issue in file_issues if issue["severity"] == "error"),
            "warnings": sum(1 for issue in file_issues if issue["severity"] == "warning"),
            "manual_review_flags": sum(1 for issue in file_issues if issue["severity"] == "manual_review"),
        })

    if tasks_path is not None:
        missing_answers = sorted(set(tasks) - answered_sources)
        for source_id in missing_answers:
            issues.append(_issue("error", "task_missing_matching_answer", "task source_id has no matching answer row", file=tasks_path, source_id=source_id, field="source_id"))

    result = _result("validate_rtl_answer_files", files, answer_count, issues, file_summaries, strict)
    _write_reports(result, output_md, output_json, "RTL Answer File Validation")
    return result, 0 if result["ok"] else 1


def _record_if_changed(changes: list[dict[str, Any]], file: Path, source_id: str | None, field: str, old: Any, new: Any, reason: str) -> None:
    if old != new:
        changes.append(_change(file, source_id, field, old, new, reason))


def _replace_text(value: Any, changes: list[dict[str, Any]], file: Path, source_id: str | None, path: str) -> Any:
    if isinstance(value, str):
        updated = re.sub(r"\bverified\s+by\s+text\s+inspection\b", "reviewed by text inspection", value, flags=re.IGNORECASE)
        _record_if_changed(changes, file, source_id, path, value, updated, "replace unsupported verified-by-text-inspection wording")
        return updated
    if isinstance(value, list):
        return [_replace_text(item, changes, file, source_id, f"{path}[]") for item in value]
    if isinstance(value, dict):
        return {key: _replace_text(item, changes, file, source_id, f"{path}.{key}") for key, item in value.items()}
    return value


def repair_answer_row(answer: dict[str, Any], file: Path, task: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[AuditIssue]]:
    repaired = deepcopy(answer)
    changes: list[dict[str, Any]] = []
    source_id = repaired.get("source_id") if isinstance(repaired.get("source_id"), str) else None

    if repaired.get("schema_version") in ANSWER_SCHEMA_ALIASES:
        old = repaired.get("schema_version")
        repaired["schema_version"] = CANONICAL_ANSWER_SCHEMA_VERSION
        changes.append(_change(file, source_id, "schema_version", old, repaired["schema_version"], "normalize obvious schema alias"))

    repaired = _replace_text(repaired, changes, file, source_id, "answer")

    for field in sorted(LIST_FIELDS):
        value = repaired.get(field)
        if isinstance(value, list):
            new_value = _dedupe_list(value)
            _record_if_changed(changes, file, source_id, field, value, new_value, f"deduplicate {field}")
            repaired[field] = new_value

    evidence = repaired.get("evidence_used")
    if isinstance(evidence, list):
        existing = [str(item) for item in evidence]
        updated = list(existing)
        if _is_synthetic_candidate(repaired, task):
            updated = [item for item in SYNTHETIC_EVIDENCE_PREFERENCE if item not in updated] + updated
        if _mentions_tool_checks_not_run(repaired) and "tool_checks" not in updated:
            updated.append("tool_checks")
        updated = _dedupe_list(updated)
        _record_if_changed(changes, file, source_id, "evidence_used", evidence, updated, "normalize evidence_used for conservative synthetic answer audit")
        repaired["evidence_used"] = updated

    limitations = repaired.get("limitations")
    if _all_tool_checks_null_or_absent(task):
        if not isinstance(limitations, list):
            old = limitations
            repaired["limitations"] = [NO_TOOL_CHECKS_LIMITATION]
            changes.append(_change(file, source_id, "limitations", old, repaired["limitations"], "add conservative no-tool-checks limitation"))
        elif not _mentions_tool_checks_not_run(repaired):
            old = list(limitations)
            repaired["limitations"] = _dedupe_list(limitations + [NO_TOOL_CHECKS_LIMITATION])
            changes.append(_change(file, source_id, "limitations", old, repaired["limitations"], "add conservative no-tool-checks limitation"))

    levels = repaired.get("claim_levels")
    if isinstance(levels, dict):
        updated_levels = dict(levels)
        if not _domain_has_evidence(task, "correctness") and updated_levels.get("correctness") not in CONSERVATIVE_CORRECTNESS_LEVELS:
            updated_levels["correctness"] = "suggestion_only"
        for domain in sorted(NON_CORRECTNESS_DOMAINS):
            if not _domain_has_evidence(task, domain) and updated_levels.get(domain) != "insufficient_evidence":
                updated_levels[domain] = "insufficient_evidence"
        _record_if_changed(changes, file, source_id, "claim_levels", levels, updated_levels, "downgrade unsupported claim levels without supplied tool evidence")
        repaired["claim_levels"] = updated_levels

    space_reasoning = repaired.get("space_reasoning")
    if not isinstance(space_reasoning, dict):
        space_reasoning = {}
        repaired["space_reasoning"] = space_reasoning
    hardware = space_reasoning.get("hardware_resources_involved")
    if not isinstance(hardware, list):
        hardware = []
    original_hardware = list(hardware)

    issues = repaired.get("issue_summary")
    if isinstance(issues, list):
        for issue_index, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            evidence_obj = issue.get("evidence")
            if not isinstance(evidence_obj, dict):
                continue
            signal_names = evidence_obj.get("signal_names")
            if not isinstance(signal_names, list):
                continue
            original_signals = list(signal_names)
            kept: list[Any] = []
            removed: list[str] = []
            for signal_name in signal_names:
                if isinstance(signal_name, str) and _is_generic_signal_label(signal_name):
                    removed.append(signal_name)
                else:
                    kept.append(signal_name)
            kept = _dedupe_list(kept)
            if removed:
                evidence_obj["signal_names"] = kept
                hardware = _dedupe_list(hardware + removed)
                changes.append(_change(
                    file,
                    source_id,
                    f"issue_summary[{issue_index}].evidence.signal_names",
                    original_signals,
                    kept,
                    "move generic labels out of signal_names",
                ))
    if hardware != original_hardware:
        space_reasoning["hardware_resources_involved"] = hardware
        changes.append(_change(file, source_id, "space_reasoning.hardware_resources_involved", original_hardware, hardware, "preserve removed generic labels as hardware context"))
    elif isinstance(space_reasoning.get("hardware_resources_involved"), list):
        deduped_hardware = _dedupe_list(space_reasoning["hardware_resources_involved"])
        _record_if_changed(changes, file, source_id, "space_reasoning.hardware_resources_involved", space_reasoning["hardware_resources_involved"], deduped_hardware, "deduplicate hardware_resources_involved")
        space_reasoning["hardware_resources_involved"] = deduped_hardware

    manual_flags = [issue for issue in validate_answer_row(repaired, file, 1, task) if issue["severity"] == "manual_review"]
    return repaired, changes, manual_flags


def _relative_output_path(path: Path, base_dir: Path | None, output_dir: Path) -> Path:
    try:
        relative = path.relative_to(base_dir) if base_dir else Path(path.name)
    except ValueError:
        relative = Path(path.name)
    return output_dir / relative


def repair_answer_files(
    inputs: list[Path] | None = None,
    input_dirs: list[Path] | None = None,
    glob_pattern: str = DEFAULT_GLOB,
    output_dir: Path | None = None,
    in_place: bool = False,
    backup: bool = True,
    tasks_path: Path | None = None,
    report_md: Path | None = None,
    report_json: Path | None = None,
    strict: bool = False,
    dry_run: bool = False,
) -> tuple[dict[str, Any], int]:
    if not in_place and not dry_run and output_dir is None:
        issues = [_issue("error", "missing_output_dir", "--output-dir is required unless --in-place or --dry-run is used", file=Path("<args>"))]
        result = _result("repair_rtl_answer_files", [], 0, issues, [], strict, changes=[])
        _write_reports(result, report_md, report_json, "RTL Answer File Repair")
        return result, 1

    files, discovery_errors = discover_answer_files(inputs, input_dirs, glob_pattern, exclude_dir=output_dir)
    tasks, task_errors = _load_tasks(tasks_path)
    issues: list[AuditIssue] = []
    for error in discovery_errors + task_errors:
        issues.append(_issue("error", "load_error", error, file=Path("<discovery>")))

    changes: list[dict[str, Any]] = []
    manual_flags: list[AuditIssue] = []
    answer_count = 0
    file_summaries: list[dict[str, Any]] = []

    for loaded in files:
        path = loaded["path"]
        rows = loaded["rows"]
        repaired_rows: list[dict[str, Any]] = []
        file_change_start = len(changes)
        file_manual_start = len(manual_flags)
        for row in rows:
            answer_count += 1
            source_id = row.get("source_id") if isinstance(row.get("source_id"), str) else None
            task = tasks.get(source_id) if source_id else None
            repaired, row_changes, row_manual_flags = repair_answer_row(row, path, task)
            repaired_rows.append(repaired)
            changes.extend(row_changes)
            manual_flags.extend(row_manual_flags)

        output_path = path if in_place else _relative_output_path(path, loaded.get("base_dir"), output_dir or Path("."))
        if not dry_run and rows:
            if in_place and backup:
                backup_path = path.with_suffix(path.suffix + ".bak")
                if not backup_path.exists():
                    shutil.copy2(path, backup_path)
            _write_answer_file(output_path, loaded, repaired_rows)
        file_summaries.append({
            "path": str(path),
            "output_path": str(output_path),
            "kind": loaded["kind"],
            "answers": len(rows),
            "automatic_repairs": len(changes) - file_change_start,
            "manual_review_flags": len(manual_flags) - file_manual_start,
        })

    issues.extend(manual_flags)
    result = _result("repair_rtl_answer_files", files, answer_count, issues, file_summaries, strict, changes=changes)
    result["dry_run"] = dry_run
    result["in_place"] = in_place
    result["output_dir"] = str(output_dir) if output_dir else None
    result["automatic_repairs"] = len(changes)
    _write_reports(result, report_md, report_json, "RTL Answer File Repair")
    return result, 0 if result["ok"] else 1


def _result(
    created_by: str,
    files: list[dict[str, Any]],
    answer_count: int,
    issues: list[AuditIssue],
    file_summaries: list[dict[str, Any]],
    strict: bool,
    changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors = [issue for issue in issues if issue["severity"] == "error"]
    warnings = [issue for issue in issues if issue["severity"] == "warning"]
    manual = [issue for issue in issues if issue["severity"] == "manual_review"]
    ok = not errors and (not strict or (not warnings and not manual))
    return {
        "ok": ok,
        "created_by": created_by,
        "files_scanned": len(files),
        "answers_scanned": answer_count,
        "errors": errors,
        "warnings": warnings,
        "manual_review_flags": manual,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "manual_review_flag_count": len(manual),
        "file_summaries": file_summaries,
        "changes": changes or [],
    }


def _write_reports(result: dict[str, Any], output_md: Path | None, output_json: Path | None, title: str) -> None:
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown_report(result, title), encoding="utf-8", newline="\n")


def _markdown_report(result: dict[str, Any], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"- OK: {str(result['ok']).lower()}",
        f"- Files scanned: {result['files_scanned']}",
        f"- Answers scanned: {result['answers_scanned']}",
        f"- Errors: {result['error_count']}",
        f"- Warnings: {result['warning_count']}",
        f"- Manual-review flags: {result['manual_review_flag_count']}",
        f"- Automatic repairs: {len(result.get('changes', []))}",
        "",
        "## Files",
        "",
    ]
    if result["file_summaries"]:
        lines.extend(
            f"- `{item['path']}`: {item.get('answers', 0)} answers"
            for item in result["file_summaries"]
        )
    else:
        lines.append("- none")
    if result.get("changes"):
        lines.extend(["", "## Automatic Repairs", ""])
        for item in result["changes"]:
            lines.append(f"- `{item['file']}` `{item.get('source_id')}` `{item['field']}`: {item['reason']}")
    for heading, key in (("Errors", "errors"), ("Warnings", "warnings"), ("Manual Review", "manual_review_flags")):
        lines.extend(["", f"## {heading}", ""])
        if result[key]:
            for issue in result[key]:
                source = f" `{issue['source_id']}`" if issue.get("source_id") else ""
                field = f" `{issue['field']}`" if issue.get("field") else ""
                lines.append(f"- `{issue['file']}`{source}{field}: {issue['code']} - {issue['message']}")
        else:
            lines.append("- none")
    return "\n".join(lines) + "\n"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--input-dir", action="append", type=Path, default=[])
    parser.add_argument("--glob", default=DEFAULT_GLOB)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")


def print_summary(result: dict[str, Any], action: str) -> None:
    print(f"RTL answer {action} {'passed' if result['ok'] else 'found issues'}.")
    print()
    print(f"Files scanned: {result['files_scanned']}")
    print(f"Answers scanned: {result['answers_scanned']}")
    print(f"Errors: {result['error_count']}")
    print(f"Warnings: {result['warning_count']}")
    print(f"Manual-review flags: {result['manual_review_flag_count']}")
    if "automatic_repairs" in result:
        print(f"Automatic repairs: {result['automatic_repairs']}")
