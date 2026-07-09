"""Assemble repaired rtl_answer.v0.1 files into one deterministic JSONL dataset."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.rtl_answer_file_audit import (
    DEFAULT_GLOB,
    discover_answer_files,
    load_tasks_by_source,
    validate_answer_row,
)


DEFAULT_PRIORITY_ORDER = ("repaired", "combined", "batch", "other")
COMBINED_MARKERS = ("assembled", "combined", "clean", "merged")
BATCH_MARKERS = ("batch_", "teacher_answer_returns", "teacher_answer_batches", "answer")


@dataclass(frozen=True)
class AnswerCandidate:
    source_id: str
    row: dict[str, Any]
    file: Path
    row_index: int
    kind: str
    priority_tier: str
    priority_rank: int
    canonical_json: str


def _issue(
    severity: str,
    code: str,
    message: str,
    *,
    file: Path,
    source_id: str | None = None,
    field: str | None = None,
    row_index: int | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "file": str(file),
        "source_id": source_id,
        "field": field,
        "row_index": row_index,
    }


def parse_priority_order(raw: str) -> list[str]:
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("--priority must contain at least one tier")
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _priority_tags(path: Path) -> set[str]:
    lowered = str(path).replace("\\", "/").lower()
    name = path.name.lower()
    tags = {"other"}
    if "repaired_rtl_answer_batches" in lowered or "/repaired/" in lowered or "repaired" in lowered:
        tags.add("repaired")
    if any(marker in name for marker in COMBINED_MARKERS) or any(marker in lowered for marker in COMBINED_MARKERS):
        tags.add("combined")
    if any(marker in name for marker in BATCH_MARKERS) or any(marker in lowered for marker in BATCH_MARKERS):
        tags.add("batch")
    return tags


def classify_priority(path: Path, priority_order: list[str]) -> tuple[str, int]:
    tags = _priority_tags(path)
    for index, tier in enumerate(priority_order):
        if tier in tags:
            return tier, index
    return "other", len(priority_order)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_summary(loaded: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(loaded["path"]),
        "kind": loaded["kind"],
        "answers": len(loaded["rows"]),
        "selected_answers": 0,
        "extra_answers_without_tasks": 0,
        "duplicate_rows_skipped": 0,
    }


def _selection_key(candidate: AnswerCandidate) -> tuple[int, str, int]:
    return (candidate.priority_rank, str(candidate.file), candidate.row_index)


def _markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# RTL Answer Dataset Assembly",
        "",
        "## Summary",
        "",
        f"- OK: {str(result['ok']).lower()}",
        f"- Safe output: {str(result['safe_output']).lower()}",
        f"- Strict OK: {str(result['strict_ok']).lower()}",
        f"- Files scanned: {result['files_scanned']}",
        f"- Answers scanned: {result['answers_scanned']}",
        f"- Selected answers: {result['selected_answers']}",
        f"- Duplicate source IDs: {result['duplicate_source_id_count']}",
        f"- Harmless duplicates: {result['harmless_duplicate_count']}",
        f"- Conflicting duplicates: {result['conflicting_duplicate_count']}",
        f"- Missing task answers: {result['missing_task_answer_count']}",
        f"- Extra answers without tasks: {result['extra_answer_without_task_count']}",
        f"- Validation errors: {result['validation_error_count']}",
        f"- Validation warnings: {result['validation_warning_count']}",
        f"- Manual-review flags: {result['manual_review_flag_count']}",
        "",
        "## Output",
        "",
        f"- Output path: `{result['output_path']}`",
        f"- Output SHA256: `{result['output_sha256'] or 'not_written'}`",
        "",
        "## Duplicate Handling",
        "",
    ]
    if result["duplicate_details"]:
        for detail in result["duplicate_details"]:
            skipped = ", ".join(
                f"{item['file']} ({item['relation']}, tier={item['priority_tier']})"
                for item in detail["skipped_duplicates"]
            ) or "none"
            lines.append(
                f"- `{detail['source_id']}` selected `{detail['selected_file']}` "
                f"(tier={detail['selected_priority_tier']}); skipped: {skipped}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Missing/Extra Rows", ""])
    if result["missing_task_answers"]:
        lines.extend(f"- missing task answer: `{source_id}`" for source_id in result["missing_task_answers"])
    else:
        lines.append("- missing task answer: none")
    if result["extra_answers_without_tasks"]:
        lines.extend(f"- extra answer without task: `{source_id}`" for source_id in result["extra_answers_without_tasks"])
    else:
        lines.append("- extra answer without task: none")
    lines.extend(["", "## Validation Issues", ""])
    if result["errors"]:
        lines.extend(
            f"- `{item['file']}` `{item.get('source_id')}` `{item.get('field')}`: {item['code']} - {item['message']}"
            for item in result["errors"]
        )
    else:
        lines.append("- errors: none")
    if result["warnings"]:
        lines.extend(
            f"- `{item['file']}` `{item.get('source_id')}` `{item.get('field')}`: {item['code']} - {item['message']}"
            for item in result["warnings"]
        )
    else:
        lines.append("- warnings: none")
    lines.extend(["", "## Manual Review Flags", ""])
    if result["manual_review_flags"]:
        lines.extend(
            f"- `{item['file']}` `{item.get('source_id')}` `{item.get('field')}`: {item['code']} - {item['message']}"
            for item in result["manual_review_flags"]
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Selected Files", ""])
    if result["selected_source_file_by_source_id"]:
        for source_id, selected_file in result["selected_source_file_by_source_id"].items():
            tier = result["selected_priority_tier_by_source_id"].get(source_id)
            skipped = result["skipped_duplicate_files_by_source_id"].get(source_id, [])
            skipped_text = ", ".join(f"{item['file']} ({item['relation']})" for item in skipped) or "none"
            lines.append(f"- `{source_id}`: `{selected_file}` (tier={tier}); skipped duplicates: {skipped_text}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _write_reports(result: dict[str, Any], report_md: Path, report_json: Path) -> None:
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(_markdown_report(result), encoding="utf-8", newline="\n")
    report_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assemble_repaired_rtl_answer_dataset(
    *,
    answers_dir: Path,
    answers_glob: str = DEFAULT_GLOB,
    tasks_path: Path,
    output_path: Path,
    report_md: Path,
    report_json: Path,
    priority: str = ",".join(DEFAULT_PRIORITY_ORDER),
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    priority_order = parse_priority_order(priority)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    manual_review_flags: list[dict[str, Any]] = []

    if not answers_dir.exists():
        errors.append(_issue("error", "answers_dir_not_found", f"answers dir not found: {answers_dir}", file=Path("<args>")))
    elif not answers_dir.is_dir():
        errors.append(_issue("error", "answers_dir_not_directory", f"answers dir is not a directory: {answers_dir}", file=Path("<args>")))
    if any(part.lower() == "golden" for part in output_path.parts):
        errors.append(_issue("error", "output_in_golden", "--output must not write into data/golden", file=Path("<args>"), field="output"))

    ordered_tasks: list[dict[str, Any]] = []
    task_by_source: dict[str, dict[str, Any]] = {}
    if tasks_path:
        ordered_tasks, task_by_source, task_errors = load_tasks_by_source(tasks_path)
        for message in task_errors:
            errors.append(_issue("error", "task_load_error", message, file=tasks_path))
    else:
        errors.append(_issue("error", "missing_tasks", "--tasks is required", file=Path("<args>"), field="tasks"))

    loaded_files, discovery_errors = discover_answer_files(input_dirs=[answers_dir], glob_pattern=answers_glob)
    for message in discovery_errors:
        errors.append(_issue("error", "answer_load_error", message, file=Path("<discovery>")))

    file_summaries = [_file_summary(loaded) for loaded in loaded_files]
    summary_by_path = {item["path"]: item for item in file_summaries}
    candidates_by_source: dict[str, list[AnswerCandidate]] = {}
    answers_scanned = 0

    for loaded in loaded_files:
        path = loaded["path"]
        summary = summary_by_path[str(path)]
        priority_tier, priority_rank = classify_priority(path, priority_order)
        for row_index, row in enumerate(loaded["rows"], 1):
            answers_scanned += 1
            source_id = row.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                errors.append(
                    _issue(
                        "error",
                        "missing_source_id",
                        "source_id must be a non-empty string",
                        file=path,
                        field="source_id",
                        row_index=row_index,
                    )
                )
                continue
            candidate = AnswerCandidate(
                source_id=source_id,
                row=row,
                file=path,
                row_index=row_index,
                kind=loaded["kind"],
                priority_tier=priority_tier,
                priority_rank=priority_rank,
                canonical_json=_canonical_json(row),
            )
            candidates_by_source.setdefault(source_id, []).append(candidate)

    selected_for_output: dict[str, AnswerCandidate] = {}
    selected_source_file_by_source_id: dict[str, str] = {}
    selected_priority_tier_by_source_id: dict[str, str] = {}
    skipped_duplicate_files_by_source_id: dict[str, list[dict[str, Any]]] = {}
    duplicate_details: list[dict[str, Any]] = []
    duplicate_source_id_count = 0
    harmless_duplicate_count = 0
    conflicting_duplicate_count = 0

    for source_id in sorted(candidates_by_source):
        candidates = sorted(candidates_by_source[source_id], key=_selection_key)
        selected = candidates[0]
        selected_matches_task = source_id in task_by_source
        if selected_matches_task:
            selected_for_output[source_id] = selected
            selected_source_file_by_source_id[source_id] = str(selected.file)
            selected_priority_tier_by_source_id[source_id] = selected.priority_tier
            summary_by_path[str(selected.file)]["selected_answers"] += 1
        else:
            summary_by_path[str(selected.file)]["extra_answers_without_tasks"] += 1

        if len(candidates) == 1:
            continue

        duplicate_source_id_count += 1
        skipped_details: list[dict[str, Any]] = []
        has_conflict = False
        for skipped in candidates[1:]:
            relation = "identical" if skipped.canonical_json == selected.canonical_json else "conflicting"
            if relation == "identical":
                harmless_duplicate_count += 1
            else:
                conflicting_duplicate_count += 1
                has_conflict = True
            summary_by_path[str(skipped.file)]["duplicate_rows_skipped"] += 1
            skipped_details.append({
                "file": str(skipped.file),
                "row_index": skipped.row_index,
                "kind": skipped.kind,
                "priority_tier": skipped.priority_tier,
                "relation": relation,
            })

        skipped_duplicate_files_by_source_id[source_id] = skipped_details
        duplicate_details.append({
            "source_id": source_id,
            "selected_file": str(selected.file),
            "selected_row_index": selected.row_index,
            "selected_priority_tier": selected.priority_tier,
            "skipped_duplicates": skipped_details,
        })
        if has_conflict:
            manual_review_flags.append(
                _issue(
                    "manual_review",
                    "duplicate_source_id_conflicting_rows",
                    "duplicate source_id has conflicting answer content; kept the highest-priority row only",
                    file=selected.file,
                    source_id=source_id,
                    field="source_id",
                    row_index=selected.row_index,
                )
            )

    missing_task_answers = [str(task.get("source_id")) for task in ordered_tasks if str(task.get("source_id")) not in selected_for_output]
    extra_answers_without_tasks = sorted(source_id for source_id in candidates_by_source if source_id not in task_by_source)
    for source_id in missing_task_answers:
        warnings.append(
            _issue(
                "warning",
                "missing_task_answer",
                "task source_id has no matching assembled answer row",
                file=tasks_path,
                source_id=source_id,
                field="source_id",
            )
        )
    for source_id in extra_answers_without_tasks:
        selected = sorted(candidates_by_source[source_id], key=_selection_key)[0]
        warnings.append(
            _issue(
                "warning",
                "extra_answer_without_task",
                "answer source_id has no matching task row and was excluded from output",
                file=selected.file,
                source_id=source_id,
                field="source_id",
                row_index=selected.row_index,
            )
        )

    selected_rows: list[dict[str, Any]] = []
    for task in ordered_tasks:
        source_id = str(task.get("source_id"))
        candidate = selected_for_output.get(source_id)
        if candidate is None:
            continue
        selected_rows.append(candidate.row)
        for issue in validate_answer_row(candidate.row, candidate.file, candidate.row_index, task):
            if issue["severity"] == "error":
                errors.append(issue)
            elif issue["severity"] == "warning":
                warnings.append(issue)
            else:
                manual_review_flags.append(issue)

    safe_output = not errors
    output_sha256: str | None = None
    if safe_output:
        write_jsonl(output_path, selected_rows)
        output_sha256 = _file_sha256(output_path)

    strict_ok = safe_output and not warnings and not manual_review_flags
    ok = strict_ok if strict else safe_output
    result = {
        "ok": ok,
        "safe_output": safe_output,
        "strict_ok": strict_ok,
        "created_by": "assemble_repaired_rtl_answer_dataset",
        "answers_dir": str(answers_dir),
        "answers_glob": answers_glob,
        "tasks_path": str(tasks_path),
        "output_path": str(output_path),
        "output_sha256": output_sha256,
        "priority_order": priority_order,
        "files_scanned": len(loaded_files),
        "answers_scanned": answers_scanned,
        "selected_answers": len(selected_rows),
        "duplicate_source_id_count": duplicate_source_id_count,
        "harmless_duplicate_count": harmless_duplicate_count,
        "conflicting_duplicate_count": conflicting_duplicate_count,
        "missing_task_answers": missing_task_answers,
        "missing_task_answer_count": len(missing_task_answers),
        "extra_answers_without_tasks": extra_answers_without_tasks,
        "extra_answer_without_task_count": len(extra_answers_without_tasks),
        "errors": errors,
        "warnings": warnings,
        "manual_review_flags": manual_review_flags,
        "validation_error_count": len(errors),
        "validation_warning_count": len(warnings),
        "manual_review_flag_count": len(manual_review_flags),
        "selected_source_file_by_source_id": selected_source_file_by_source_id,
        "selected_priority_tier_by_source_id": selected_priority_tier_by_source_id,
        "skipped_duplicate_files_by_source_id": skipped_duplicate_files_by_source_id,
        "duplicate_details": duplicate_details,
        "file_summaries": file_summaries,
    }
    _write_reports(result, report_md, report_json)
    return result, 0 if ok else 1
