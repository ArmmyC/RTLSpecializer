"""Read-only readiness checks for manually reviewed public dataset batches."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any

from .io_utils import load_jsonl, write_jsonl
from .review_promotion import is_stub_answer, public_promotion_errors
from .validation import has_passing_tool_evidence, has_tool_evidence, validate_dataset_file


@dataclass(frozen=True)
class LoadResult:
    selected_rows: list[dict[str, Any]]
    reviewed_rows: list[dict[str, Any]]
    selected_errors: list[str]
    selected_warnings: list[str]
    reviewed_errors: list[str]
    reviewed_warnings: list[str]
    selected_errors_by_id: dict[str, list[str]]
    reviewed_errors_by_id: dict[str, list[str]]


def _messages_by_id(messages: list[Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for message in messages:
        if message.row_id:
            result.setdefault(message.row_id, []).append(message.format())
    return result


def load_review_files(selected_path: Path, reviewed_path: Path) -> LoadResult:
    """Load both JSONL inputs and run the existing strict dataset validator."""
    selected_loaded, selected_problems = load_jsonl(selected_path)
    reviewed_loaded, reviewed_problems = load_jsonl(reviewed_path)
    selected_report = validate_dataset_file(selected_path, strict=True)
    reviewed_report = validate_dataset_file(reviewed_path, strict=True)
    selected_errors = [message.format() for message in selected_report.errors]
    reviewed_errors = [message.format() for message in reviewed_report.errors]
    # Read problems are already represented by validation, but retain any unique text.
    for problem in selected_problems:
        text = f"{selected_path}: {problem.message}"
        if not any(problem.message in item for item in selected_errors):
            selected_errors.append(text)
    for problem in reviewed_problems:
        text = f"{reviewed_path}: {problem.message}"
        if not any(problem.message in item for item in reviewed_errors):
            reviewed_errors.append(text)
    return LoadResult(
        selected_rows=[row for _, row in selected_loaded],
        reviewed_rows=[row for _, row in reviewed_loaded],
        selected_errors=selected_errors,
        selected_warnings=[message.format() for message in selected_report.warnings],
        reviewed_errors=reviewed_errors,
        reviewed_warnings=[message.format() for message in reviewed_report.warnings],
        selected_errors_by_id=_messages_by_id(selected_report.errors + selected_report.warnings),
        reviewed_errors_by_id=_messages_by_id(reviewed_report.errors + reviewed_report.warnings),
    )


def _duplicates(rows: list[dict[str, Any]]) -> list[str]:
    counts = Counter(row.get("id") for row in rows if isinstance(row.get("id"), str))
    return sorted(row_id for row_id, count in counts.items() if count > 1)


def _answer(row: dict[str, Any]) -> dict[str, Any] | None:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 3 or not isinstance(messages[2], dict):
        return None
    answer = messages[2].get("content")
    return answer if isinstance(answer, dict) else None


def _claim_risks(row: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    levels = answer.get("claim_levels")
    risks: list[str] = []
    if isinstance(levels, dict):
        if levels.get("correctness") == "verified" and not any(
            has_passing_tool_evidence(row, tool) for tool in ("simulation", "equivalence")
        ):
            risks.append("correctness verified without passing simulation or equivalence evidence")
        for domain, tools in {
            "area": ("synthesis",), "activity": ("toggle",), "power": ("power",)
        }.items():
            if levels.get(domain) not in {"insufficient_evidence", "not_applicable"} and not any(
                has_tool_evidence(row, tool) for tool in tools
            ):
                risks.append(f"{domain} claim is stronger than insufficient_evidence without relevant tool/report evidence")
    plan = answer.get("verification_plan")
    plan_text = " ".join(str(item).lower() for item in plan) if isinstance(plan, list) else ""
    if "lint" not in plan_text and "compile" not in plan_text:
        risks.append("verification_plan is missing lint/compile")
    return risks


def check_review_readiness(
    selected_rows: list[dict[str, Any]],
    reviewed_rows: list[dict[str, Any]],
    *,
    selected_validation_errors: dict[str, list[str]] | None = None,
    reviewed_validation_errors: dict[str, list[str]] | None = None,
    selected_file_errors: list[str] | None = None,
    reviewed_file_errors: list[str] | None = None,
    selected_file_warnings: list[str] | None = None,
    reviewed_file_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Compare rows and apply promotion gates without promoting or mutating data."""
    if (
        selected_validation_errors is None and reviewed_validation_errors is None
        and selected_file_errors is None and reviewed_file_errors is None
        and selected_file_warnings is None and reviewed_file_warnings is None
    ):
        with tempfile.TemporaryDirectory(prefix="rtl-review-readiness-") as directory:
            selected_temp = Path(directory) / "selected.jsonl"
            reviewed_temp = Path(directory) / "reviewed.jsonl"
            write_jsonl(selected_temp, selected_rows)
            write_jsonl(reviewed_temp, reviewed_rows)
            selected_report = validate_dataset_file(selected_temp, strict=True)
            reviewed_report = validate_dataset_file(reviewed_temp, strict=True)
            selected_validation_errors = _messages_by_id(selected_report.errors + selected_report.warnings)
            reviewed_validation_errors = _messages_by_id(reviewed_report.errors + reviewed_report.warnings)
            selected_file_errors = [message.format() for message in selected_report.errors]
            reviewed_file_errors = [message.format() for message in reviewed_report.errors]
            selected_file_warnings = [message.format() for message in selected_report.warnings]
            reviewed_file_warnings = [message.format() for message in reviewed_report.warnings]
    selected_validation_errors = selected_validation_errors or {}
    reviewed_validation_errors = reviewed_validation_errors or {}
    selected_file_errors = selected_file_errors or []
    reviewed_file_errors = reviewed_file_errors or []
    selected_file_warnings = selected_file_warnings or []
    reviewed_file_warnings = reviewed_file_warnings or []
    selected_duplicates = _duplicates(selected_rows)
    reviewed_duplicates = _duplicates(reviewed_rows)
    selected_by_id = {
        row["id"]: row for row in selected_rows
        if isinstance(row.get("id"), str) and row["id"] not in selected_duplicates
    }
    reviewed_by_id = {
        row["id"]: row for row in reviewed_rows
        if isinstance(row.get("id"), str) and row["id"] not in reviewed_duplicates
    }
    selected_ids = set(selected_by_id)
    reviewed_ids = set(reviewed_by_id)
    missing = sorted(selected_ids - reviewed_ids)
    extra = sorted(reviewed_ids - selected_ids)
    matched = sorted(selected_ids & reviewed_ids)
    row_results: list[dict[str, Any]] = []
    for row_id in matched:
        selected = selected_by_id[row_id]
        reviewed = reviewed_by_id[row_id]
        selected_answer = _answer(selected)
        reviewed_answer = _answer(reviewed)
        changed = reviewed_answer is not None and reviewed_answer != selected_answer
        stub = reviewed_answer is None or is_stub_answer(reviewed_answer)
        validation_errors = list(reviewed_validation_errors.get(row_id, []))
        promotion_errors = public_promotion_errors(reviewed, allow_stub_answer=False)
        warnings = _claim_risks(reviewed, reviewed_answer) if reviewed_answer is not None else []
        ready = changed and not stub and not validation_errors and not promotion_errors and not warnings
        if ready:
            action = "Ready for promotion after all intended rows are ready."
        elif not changed or stub:
            action = "Edit assistant answer with concrete signal-grounded reasoning."
        elif validation_errors:
            action = "Fix structural validation errors, then rerun the readiness check."
        else:
            action = "Address promotion gates and claim-safety warnings, then rerun the readiness check."
        row_results.append({
            "id": row_id,
            "ready": ready,
            "changed_from_selected": changed,
            "is_stub_answer": stub,
            "claim_levels": reviewed_answer.get("claim_levels", {}) if reviewed_answer else {},
            "promotion_errors": promotion_errors,
            "validation_errors": validation_errors,
            "warnings": warnings,
            "suggested_next_action": action,
        })
    ready_rows = sum(1 for row in row_results if row["ready"])
    errors = list(selected_file_errors) + list(reviewed_file_errors)
    errors.extend(f"duplicate selected row id: {row_id}" for row_id in selected_duplicates)
    errors.extend(f"duplicate reviewed row id: {row_id}" for row_id in reviewed_duplicates)
    warnings = list(selected_file_warnings) + list(reviewed_file_warnings)
    return {
        "ok": not errors and bool(matched),
        "all_rows_ready": (
            bool(matched) and ready_rows == len(row_results)
            and not missing and not extra and not errors and not warnings
        ),
        "selected_rows": len(selected_rows),
        "reviewed_rows": len(reviewed_rows),
        "matched_rows": len(matched),
        "ready_rows": ready_rows,
        "needs_work_rows": len(row_results) - ready_rows,
        "missing_reviewed_rows": missing,
        "extra_reviewed_rows": extra,
        "duplicate_selected_ids": selected_duplicates,
        "duplicate_reviewed_ids": reviewed_duplicates,
        "selected_validation_errors": list(selected_file_errors),
        "reviewed_validation_errors": list(reviewed_file_errors),
        "errors": errors,
        "warnings": warnings,
        "rows": row_results,
    }


def _markdown(result: dict[str, Any]) -> str:
    ready = [row for row in result["rows"] if row["ready"]]
    needs_work = [row for row in result["rows"] if not row["ready"]]
    lines = [
        "# Review batch readiness report", "", "## Summary", "",
        f"- Selected rows: {result['selected_rows']}",
        f"- Reviewed rows: {result['reviewed_rows']}",
        f"- Matched rows: {result['matched_rows']}",
        f"- Ready rows: {result['ready_rows']}",
        f"- Needs work: {result['needs_work_rows']}",
        f"- Missing reviewed rows: {len(result['missing_reviewed_rows'])}",
        f"- Extra reviewed rows: {len(result['extra_reviewed_rows'])}", "",
        "## Rows ready", "", "| ID | Status |", "| --- | --- |",
    ]
    lines.extend(f"| `{row['id']}` | Ready |" for row in ready)
    if not ready:
        lines.append("| — | None |")
    lines.extend(["", "## Rows needing work", "", "| ID | Reasons | Next action |", "| --- | --- | --- |"])
    for row in needs_work:
        reasons = row["validation_errors"] + row["promotion_errors"] + row["warnings"]
        if not row["changed_from_selected"]:
            reasons.insert(0, "assistant answer is unchanged")
        reason_text = "; ".join(reasons) or "not ready"
        escaped_reason_text = reason_text.replace("|", "\\|")
        lines.append(f"| `{row['id']}` | {escaped_reason_text} | {row['suggested_next_action']} |")
    if not needs_work:
        lines.append("| — | None | — |")
    lines.extend(["", "## Missing and extra rows", ""])
    lines.append("- Missing: " + (", ".join(f"`{item}`" for item in result["missing_reviewed_rows"]) or "none"))
    lines.append("- Extra: " + (", ".join(f"`{item}`" for item in result["extra_reviewed_rows"]) or "none"))
    common = Counter(error for row in needs_work for error in row["promotion_errors"])
    lines.extend(["", "## Common promotion errors", ""])
    lines.extend(f"- {error} ({count})" for error, count in common.most_common())
    if not common:
        lines.append("- none")
    lines.extend([
        "", "## Next steps", "",
        "Manually edit `reviewed_rows.jsonl`, then rerun this readiness check. Unchanged imported stubs are not promotion-ready.", "",
        "Only after every intended row is ready, run:", "", "```bash",
        "python scripts/dataset/promote_reviewed_rows.py \\",
        "  --input data/review/verilog_eval_batch_001/reviewed_rows.jsonl \\",
        "  --output data/processed/verilog_eval_validated_v0.1.jsonl \\",
        "  --report data/reports/verilog_eval_validated_v0.1_report.json \\",
        "  --json", "```", "",
    ])
    return "\n".join(lines)


def write_readiness_reports(
    result: dict[str, Any], output_json: Path | None, output_md: Path | None
) -> None:
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_markdown(result), encoding="utf-8", newline="\n")
