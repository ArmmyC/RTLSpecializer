"""Deterministic, read-only triage for selected and reviewed dataset batches."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .validation import has_passing_tool_evidence


SEVERITY_ORDER = {"critical": 0, "important": 1, "minor": 2}
ANSWER_SECTIONS = {
    "issue_summary",
    "time_reasoning",
    "space_reasoning",
    "safe_optimization",
    "functional_risk",
    "verification_plan",
    "claim_levels",
    "patch",
}
PLACEHOLDER_RE = re.compile(
    r"\b(?:placeholder|not\s+recovered|restore\s+(?:the\s+)?exact\s+original|"
    r"todo|missing\s+original\s+artifacts)\b",
    re.IGNORECASE,
)
SOFT_CLAIM_RE = re.compile(
    r"\b(?:by\s+text\s+inspection|appears|insufficient\s+evidence|"
    r"not\s+verified|unverified|without\s+(?:tool\s+)?evidence|"
    r"no\s+(?:simulation|synthesis|report|evidence)|may|might|could)\b",
    re.IGNORECASE,
)


def _issue(severity: str, code: str, message: str, suggested_action: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "suggested_action": suggested_action,
    }


def _row_id(row: dict[str, Any], index: int, source: str) -> str:
    value = row.get("id")
    return value if isinstance(value, str) and value else f"{source}:line-{index}"


def _duplicates(rows: list[dict[str, Any]]) -> list[str]:
    counts = Counter(row.get("id") for row in rows if isinstance(row.get("id"), str))
    return sorted(row_id for row_id, count in counts.items() if count > 1)


def _messages(row: dict[str, Any]) -> list[Any] | None:
    value = row.get("messages")
    return value if isinstance(value, list) else None


def _content(messages: list[Any], index: int) -> Any:
    if len(messages) <= index or not isinstance(messages[index], dict):
        return None
    return messages[index].get("content")


def _task_text(task: dict[str, Any]) -> tuple[str, str, str]:
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), dict) else {}
    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        prompt = artifacts.get("lint_log")
    rtl = artifacts.get("rtl_code") or artifacts.get("before_rtl_code") or artifacts.get("after_rtl_code")
    testbench = artifacts.get("testbench")
    return (
        prompt.strip() if isinstance(prompt, str) else "",
        rtl.strip() if isinstance(rtl, str) else "",
        testbench.strip() if isinstance(testbench, str) else "",
    )


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _has_evidence(row: dict[str, Any], *tools: str) -> bool:
    return any(has_passing_tool_evidence(row, tool) for tool in tools)


def _claim_issues(row: dict[str, Any], answer: dict[str, Any]) -> list[dict[str, str]]:
    checks = (
        ("unverified_correctness_wording", r"\b(?:verified|passes|passed\s+simulation|equivalent|proven)\b", ("simulation", "equivalence"), "simulation or equivalence"),
        ("unsupported_synthesis_wording", r"\b(?:synthesis\s+result|timing\s+met|area\s+reduced)\b", ("synthesis",), "synthesis"),
        ("unsupported_activity_wording", r"\b(?:activity\s+reduced|toggle\s+(?:reduced|improved))\b", ("toggle",), "toggle/activity"),
        ("unsupported_power_wording", r"\bpower\s+(?:reduced|improved)\b", ("power",), "power"),
    )
    issues: list[dict[str, str]] = []
    for value in _strings(answer):
        lowered = value.lower()
        if SOFT_CLAIM_RE.search(lowered):
            continue
        for code, pattern, tools, evidence_name in checks:
            if re.search(pattern, lowered, re.IGNORECASE) and not _has_evidence(row, *tools):
                issues.append(_issue(
                    "important",
                    code,
                    f"Strong {evidence_name} claim wording appears without passing {evidence_name} evidence.",
                    f"Soften the claim or add only real {evidence_name} evidence metadata.",
                ))
    return _dedupe_issues(issues)


def _answer_says(answer_text: str, kind: str) -> bool:
    if kind == "synchronous":
        return bool(re.search(r"(?<!a)\bsynchronous(?:\s+\w+){0,2}\s+reset\b", answer_text))
    return bool(re.search(r"\basynchronous(?:\s+\w+){0,2}\s+reset\b", answer_text))


def _reset_issues(task: dict[str, Any], answer: dict[str, Any]) -> list[dict[str, str]]:
    prompt, rtl, testbench = _task_text(task)
    context = "\n".join((prompt, rtl, testbench)).lower()
    answer_text = "\n".join(_strings(answer)).lower()
    issues: list[dict[str, str]] = []
    async_context = bool(re.search(r"\basynchronous\b", context)) or "posedge areset" in context
    sync_context = bool(re.search(r"\bsynchronous\b", context)) or (
        bool(re.search(r"always\s*@\s*\(\s*posedge\s+clk\s*\)", rtl, re.IGNORECASE))
        and "posedge areset" not in rtl.lower()
    )
    if async_context and _answer_says(answer_text, "synchronous"):
        issues.append(_issue(
            "important", "reset_async_sync_contradiction",
            "Task artifacts indicate asynchronous reset, but the answer says synchronous reset.",
            "Recheck reset wording against the prompt, RTL, and testbench before final review.",
        ))
    if sync_context and _answer_says(answer_text, "asynchronous"):
        issues.append(_issue(
            "important", "reset_sync_async_contradiction",
            "Task artifacts indicate synchronous reset, but the answer says asynchronous reset.",
            "Recheck reset wording against the prompt, RTL, and testbench before final review.",
        ))
    if "reset_test(1" in testbench.lower() and _answer_says(answer_text, "synchronous"):
        issues.append(_issue(
            "important", "testbench_reset_mode_contradiction",
            "The testbench requests asynchronous reset testing, but the answer says synchronous reset.",
            "Recheck reset wording against the supplied testbench before final review.",
        ))
    if "reset_test(0" in testbench.lower() and _answer_says(answer_text, "asynchronous"):
        issues.append(_issue(
            "important", "testbench_reset_mode_contradiction",
            "The testbench requests synchronous reset testing, but the answer says asynchronous reset.",
            "Recheck reset wording against the supplied testbench before final review.",
        ))
    aliases_reset = bool(re.search(r"(?:assign\s+)?areset\s*=\s*reset|(?:assign\s+)?reset\s*=\s*areset", testbench, re.IGNORECASE))
    if "areset" in context and "areset" not in answer_text and "reset" in answer_text and not aliases_reset:
        issues.append(_issue(
            "minor", "reset_signal_name_uncertain",
            "Task artifacts use areset while the answer refers only to reset; no testbench alias was found.",
            "Confirm the reset signal name and polarity during human review.",
        ))
    return _dedupe_issues(issues)


def _answer_quality_issues(answer: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    missing = sorted(section for section in ANSWER_SECTIONS if section not in answer)
    if missing:
        issues.append(_issue(
            "important", "missing_answer_sections",
            "Assistant answer is missing required sections: " + ", ".join(missing) + ".",
            "Restore the missing answer sections before considering readiness.",
        ))
    issue_summary = answer.get("issue_summary")
    if not isinstance(issue_summary, list) or not issue_summary:
        issues.append(_issue(
            "minor", "empty_issue_summary",
            "issue_summary is empty; this may be intentional for a no-bug review but is weak training context.",
            "For a no-bug finding, add a low-severity, signal-grounded explanation after human review.",
        ))
    elif isinstance(issue_summary, list):
        for index, item in enumerate(issue_summary):
            evidence = item.get("evidence") if isinstance(item, dict) else None
            signals = evidence.get("signal_names") if isinstance(evidence, dict) else None
            if not isinstance(signals, list) or not any(isinstance(signal, str) and signal.strip() for signal in signals):
                issues.append(_issue(
                    "minor", "issue_missing_signal_names",
                    f"issue_summary[{index}] has no concrete signal names.",
                    "Name the relevant signals or artifact fields after human review.",
                ))
    space = answer.get("space_reasoning")
    resources = space.get("hardware_resources_involved") if isinstance(space, dict) else None
    if isinstance(resources, list):
        normalized = [item.strip().lower() for item in resources if isinstance(item, str) and item.strip()]
        if len(normalized) != len(set(normalized)):
            issues.append(_issue(
                "minor", "duplicate_hardware_resources",
                "space_reasoning.hardware_resources_involved contains duplicate resource names.",
                "Remove duplicate resource names while preserving the reviewer’s reasoning.",
            ))
    plan = answer.get("verification_plan")
    plan_text = " ".join(item for item in plan if isinstance(item, str)).lower() if isinstance(plan, list) else ""
    if "lint" not in plan_text and "compile" not in plan_text:
        issues.append(_issue(
            "minor", "verification_plan_missing_lint_compile",
            "verification_plan does not mention lint or compile.",
            "Add an appropriate lint or compile check to the human-reviewed verification plan.",
        ))
    patch = answer.get("patch") if isinstance(answer.get("patch"), dict) else {}
    safe = answer.get("safe_optimization") if isinstance(answer.get("safe_optimization"), dict) else {}
    provided = patch.get("provided")
    style = safe.get("patch_style")
    if provided is True and patch.get("diff") is None:
        issues.append(_issue(
            "important", "provided_patch_missing_diff",
            "patch.provided is true but patch.diff is null.",
            "Supply the reviewed diff or mark the patch as not provided.",
        ))
    if provided is True and style in {"none", "explanation_only"}:
        issues.append(_issue(
            "important", "patch_style_inconsistent",
            "safe_optimization.patch_style is inconsistent with a provided patch.",
            "Align patch_style with the actual reviewed patch state.",
        ))
    if provided is False and style in {"code_patch", "diff"}:
        issues.append(_issue(
            "minor", "patch_style_inconsistent",
            "safe_optimization.patch_style suggests a code patch, but patch.provided is false.",
            "Align patch_style with the actual reviewed patch state.",
        ))
    return issues


def _dedupe_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for issue in issues:
        key = (issue["code"], issue["message"])
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return sorted(result, key=lambda item: (SEVERITY_ORDER[item["severity"]], item["code"], item["message"]))


def _shape_issues(row: dict[str, Any], source: str) -> list[dict[str, str]]:
    messages = _messages(row)
    if messages is None:
        return [_issue("important", f"{source}_messages_missing", f"{source} row has no messages list.", "Restore the system, user, and assistant messages.")]
    if len(messages) < 3:
        return [_issue("important", f"{source}_messages_incomplete", f"{source} row has fewer than three messages.", "Restore system, user, and assistant messages in that order.")]
    roles = [message.get("role") if isinstance(message, dict) else None for message in messages[:3]]
    if roles != ["system", "user", "assistant"]:
        return [_issue("important", f"{source}_message_role_order", f"{source} first three roles are {roles!r}, expected ['system', 'user', 'assistant'].", "Restore the required system, user, assistant message order.")]
    return []


def _reviewed_row_issues(selected: dict[str, Any] | None, reviewed: dict[str, Any]) -> list[dict[str, str]]:
    issues = _shape_issues(reviewed, "reviewed")
    messages = _messages(reviewed)
    if messages is None or len(messages) < 2:
        return issues
    user_content = _content(messages, 1)
    assistant_content = _content(messages, 2)
    if not isinstance(user_content, dict):
        issues.append(_issue("important", "user_content_not_object", "reviewed user.content is not an object.", "Restore the rtl_task_v0.1 task object."))
        return _dedupe_issues(issues)
    user_schema = user_content.get("schema_version")
    if user_schema == "rtl_answer_v0.1":
        issues.append(_issue("important", "user_content_is_answer", "user.content is rtl_answer_v0.1 instead of a task.", "Restore the original rtl_task_v0.1 user message with prompt/spec and artifacts."))
    elif user_schema != "rtl_task_v0.1":
        issues.append(_issue("important", "user_content_not_task", "user.content is missing rtl_task_v0.1 schema_version.", "Restore the original rtl_task_v0.1 user message with prompt/spec and artifacts."))
    if isinstance(assistant_content, dict) and user_content == assistant_content:
        issues.append(_issue("critical", "duplicated_user_assistant_content", "user.content exactly duplicates assistant.content.", "Restore the original rtl_task_v0.1 user message; keep the rtl_answer_v0.1 only in assistant.content."))
    prompt, rtl, testbench = _task_text(user_content)
    artifacts_text = "\n".join((prompt, rtl, testbench))
    if PLACEHOLDER_RE.search(artifacts_text):
        issues.append(_issue("critical", "placeholder_task_artifacts", "Task prompt or artifacts contain recovery/placeholder text.", "Restore the exact original prompt/spec, RTL, and checker artifacts."))
    if not prompt:
        issues.append(_issue("important", "missing_task_prompt", "Task prompt/spec text is missing or empty.", "Restore the original prompt/specification."))
    if not rtl:
        issues.append(_issue("important", "missing_task_rtl", "Task RTL artifact text is missing or empty.", "Restore the original RTL artifact."))
    if selected is not None:
        selected_messages = _messages(selected)
        selected_task = _content(selected_messages, 1) if selected_messages else None
        if isinstance(selected_task, dict):
            _, _, selected_testbench = _task_text(selected_task)
            if selected_testbench and not testbench:
                issues.append(_issue("important", "missing_task_testbench", "Reviewed row is missing a testbench/checker present in the selected row.", "Restore the selected-row testbench/checker artifact."))
    if not isinstance(assistant_content, dict):
        issues.append(_issue("important", "assistant_content_not_object", "reviewed assistant.content is not an answer object.", "Restore the rtl_answer_v0.1 assistant answer object."))
    elif assistant_content.get("schema_version") == "rtl_answer_v0.1":
        issues.extend(_answer_quality_issues(assistant_content))
        issues.extend(_claim_issues(reviewed, assistant_content))
        issues.extend(_reset_issues(user_content, assistant_content))
    else:
        issues.append(_issue("important", "assistant_content_not_answer", "reviewed assistant.content is missing rtl_answer_v0.1 schema_version.", "Restore the assistant rtl_answer_v0.1 content."))
    return _dedupe_issues(issues)


def triage_review_batch(
    selected_rows: list[dict[str, Any]], reviewed_rows: list[dict[str, Any]], *, file_issues: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """Return deterministic triage findings without mutating any input rows."""
    selected_duplicates = _duplicates(selected_rows)
    reviewed_duplicates = _duplicates(reviewed_rows)
    selected_by_id = {row["id"]: row for row in selected_rows if isinstance(row.get("id"), str) and row["id"] not in selected_duplicates}
    reviewed_by_id = {row["id"]: row for row in reviewed_rows if isinstance(row.get("id"), str) and row["id"] not in reviewed_duplicates}
    row_issues: dict[str, list[dict[str, str]]] = {}

    def add(row_id: str, issues: list[dict[str, str]]) -> None:
        if issues:
            row_issues.setdefault(row_id, []).extend(issues)

    for index, row in enumerate(selected_rows, 1):
        add(_row_id(row, index, "selected"), _shape_issues(row, "selected"))
    for row_id in selected_duplicates:
        add(row_id, [_issue("important", "duplicate_selected_id", f"Selected input contains duplicate row id `{row_id}`.", "Remove or restore the duplicate selected row before review.")])
    for row_id in reviewed_duplicates:
        add(row_id, [_issue("important", "duplicate_reviewed_id", f"Reviewed input contains duplicate row id `{row_id}`.", "Remove or restore the duplicate reviewed row before review.")])
    missing = sorted(set(selected_by_id) - set(reviewed_by_id))
    extra = sorted(set(reviewed_by_id) - set(selected_by_id))
    for row_id in missing:
        add(row_id, [_issue("important", "missing_reviewed_row", "Selected row is missing from reviewed_rows.jsonl.", "Restore the reviewed row with the same id." )])
    for row_id in extra:
        add(row_id, [_issue("important", "extra_reviewed_row", "Reviewed row does not exist in selected_rows.jsonl.", "Remove the extra row or add the corresponding selected row." )])
    for row_id in sorted(reviewed_by_id):
        add(row_id, _reviewed_row_issues(selected_by_id.get(row_id), reviewed_by_id[row_id]))
    if file_issues:
        add("<input>", file_issues)
    rows = [
        {"id": row_id, "severity": min((issue["severity"] for issue in issues), key=SEVERITY_ORDER.__getitem__), "issues": _dedupe_issues(issues)}
        for row_id, issues in sorted(row_issues.items())
    ]
    all_issues = [issue for row in rows for issue in row["issues"]]
    return {
        "ok": not any(issue["severity"] == "critical" and row["id"] == "<input>" for row in rows for issue in row["issues"]),
        "selected_rows": len(selected_rows),
        "reviewed_rows": len(reviewed_rows),
        "missing_reviewed_rows": missing,
        "extra_reviewed_rows": extra,
        "duplicate_selected_ids": selected_duplicates,
        "duplicate_reviewed_ids": reviewed_duplicates,
        "critical_count": sum(issue["severity"] == "critical" for issue in all_issues),
        "important_count": sum(issue["severity"] == "important" for issue in all_issues),
        "minor_count": sum(issue["severity"] == "minor" for issue in all_issues),
        "rows": rows,
    }


def _issue_table(rows: list[dict[str, Any]], severity: str) -> list[str]:
    entries = [(row["id"], issue) for row in rows for issue in row["issues"] if issue["severity"] == severity]
    lines = [f"## {severity.title()} issues", "", "| Row | Code | Finding | Suggested action |", "| --- | --- | --- | --- |"]
    if not entries:
        return lines + ["| â€” | None | None | â€” |", ""]
    for row_id, issue in entries:
        message = issue["message"].replace("|", "\\|")
        action = issue["suggested_action"].replace("|", "\\|")
        lines.append(f"| `{row_id}` | `{issue['code']}` | {message} | {action} |")
    return lines + [""]


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Review batch triage report", "", "## Summary", "",
        f"- Selected rows: {result['selected_rows']}",
        f"- Reviewed rows: {result['reviewed_rows']}",
        f"- Critical issues: {result['critical_count']}",
        f"- Important issues: {result['important_count']}",
        f"- Minor issues: {result['minor_count']}",
        f"- Missing reviewed rows: {len(result['missing_reviewed_rows'])}",
        f"- Extra reviewed rows: {len(result['extra_reviewed_rows'])}", "",
        "Triage flags items for human attention. It does not approve, reject, promote, or rewrite rows.", "",
    ]
    for severity in ("critical", "important", "minor"):
        lines.extend(_issue_table(result["rows"], severity))
    lines.extend([
        "## Next steps", "",
        "Review each flagged item against the original prompt/spec and artifacts. Make any content decisions manually, then rerun triage and the readiness checker before promotion.", "",
    ])
    return "\n".join(lines)


def write_triage_reports(result: dict[str, Any], output_json: Path | None, output_md: Path | None) -> None:
    """Write only explicitly requested deterministic report outputs."""
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown(result), encoding="utf-8", newline="\n")
