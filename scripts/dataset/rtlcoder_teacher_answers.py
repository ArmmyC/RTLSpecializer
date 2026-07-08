"""RTLCoder-specific teacher-answer export, validation, and draft merge helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.dataset.constants import TEACHER_DISTILL_REVIEW_STATUS
from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.rtl_answer_teacher_batches import (
    DEFAULT_SYSTEM_PROMPT,
    _load_answer_rows,
    _load_task_rows,
    _task_by_source,
    export_rtl_answer_teacher_batches,
    validate_rtl_answer_teacher_batch,
)


DEFAULT_BATCH_SIZE = 10
APPROVAL_STATUS = "not_approved"
MERGE_CREATED_BY = "merge_rtlcoder_teacher_distill_rows"
SOURCE = "teacher_generated"


def _is_local_data_path(path: Path) -> bool:
    return any(part.lower() == ".local_data" for part in path.parts)


def _is_golden_path(path: Path) -> bool:
    return any(part.lower() == "golden" for part in path.parts)


def export_rtlcoder_teacher_answer_batches(
    input_path: Path,
    output_dir: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    start_index: int = 0,
    force: bool = False,
) -> tuple[dict[str, Any], int]:
    result, code = export_rtl_answer_teacher_batches(
        input_path=input_path,
        output_dir=output_dir,
        batch_size=batch_size,
        limit=limit,
        start_index=start_index,
        force=force,
    )
    result = {
        **result,
        "workflow": "rtlcoder_teacher_answer",
        "default_batch_size": DEFAULT_BATCH_SIZE,
    }
    return result, code


def validate_rtlcoder_teacher_answers(
    tasks_path: Path,
    answers_path: Path,
    output_md: Path | None = None,
    output_json: Path | None = None,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    result, code = validate_rtl_answer_teacher_batch(
        tasks_path=tasks_path,
        answers_path=answers_path,
        output_md=output_md,
        output_json=output_json,
        strict=strict,
    )
    result = {
        **result,
        "workflow": "rtlcoder_teacher_answer",
    }
    return result, code


def merge_rtlcoder_teacher_distill_rows(
    tasks_path: Path,
    answers_path: Path,
    output_path: Path,
    system_prompt_path: Path | None = None,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        resolved_output = output_path.resolve()
    except OSError as exc:
        return _merge_result(
            False,
            tasks_path,
            answers_path,
            output_path,
            0,
            [f"could not resolve --output: {exc}"],
            warnings,
        ), 1

    if _is_local_data_path(resolved_output):
        errors.append("--output must not be inside .local_data")
    if _is_golden_path(resolved_output):
        errors.append("--output must not write into data/golden")
    if output_path.exists() and output_path.is_dir():
        errors.append("--output exists and is a directory")
    if output_path.exists() and output_path.is_symlink():
        errors.append("--output must not be a symlink")
    if errors:
        return _merge_result(False, tasks_path, answers_path, output_path, 0, errors, warnings), 1

    validation, validation_code = validate_rtlcoder_teacher_answers(
        tasks_path=tasks_path,
        answers_path=answers_path,
        strict=strict,
    )
    if validation_code != 0:
        errors.extend(validation["errors"])
        warnings.extend(validation["warnings"])
        return _merge_result(False, tasks_path, answers_path, output_path, 0, errors, warnings), 1

    tasks, task_errors = _load_task_rows(tasks_path)
    answers, answer_errors, _ = _load_answer_rows(answers_path)
    errors.extend(task_errors)
    errors.extend(answer_errors)
    task_by_source, task_source_errors = _task_by_source(tasks)
    errors.extend(task_source_errors)
    if errors:
        return _merge_result(False, tasks_path, answers_path, output_path, 0, errors, warnings), 1

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if system_prompt_path is not None:
        if system_prompt_path.is_symlink():
            return _merge_result(
                False,
                tasks_path,
                answers_path,
                output_path,
                0,
                ["--system-prompt must not be a symlink"],
                warnings,
            ), 1
        try:
            system_prompt = system_prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return _merge_result(
                False,
                tasks_path,
                answers_path,
                output_path,
                0,
                [f"could not read --system-prompt: {exc}"],
                warnings,
            ), 1
        if not system_prompt.strip():
            return _merge_result(
                False,
                tasks_path,
                answers_path,
                output_path,
                0,
                ["--system-prompt must not be empty"],
                warnings,
            ), 1

    rows: list[dict[str, Any]] = []
    for answer in answers:
        source_id = answer.get("source_id")
        task = task_by_source.get(source_id)
        if task is None:
            continue
        row = {
            "source_id": source_id,
            "source": SOURCE,
            "source_dataset": task.get("source_dataset"),
            "license": task.get("license"),
            "design_family": task.get("design_family"),
            "task_family": task.get("task_type"),
            "synthetic_bug": task.get("synthetic_bug") is True,
            "created_by": MERGE_CREATED_BY,
            "review_status": TEACHER_DISTILL_REVIEW_STATUS,
            "approval_status": APPROVAL_STATUS,
            "promotion_allowed": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": deepcopy(task)},
                {"role": "assistant", "content": deepcopy(answer)},
            ],
        }
        provenance = task.get("provenance")
        if isinstance(provenance, dict):
            row["provenance"] = deepcopy(provenance)
        rows.append(row)

    write_jsonl(output_path, rows)
    return _merge_result(False if errors else True, tasks_path, answers_path, output_path, len(rows), errors, warnings), 0 if not errors else 1


def _merge_result(
    ok: bool,
    tasks_path: Path,
    answers_path: Path,
    output_path: Path,
    rows_written: int,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tasks": str(tasks_path),
        "answers": str(answers_path),
        "output": str(output_path),
        "rows_written": rows_written,
        "created_by": MERGE_CREATED_BY,
        "review_status": TEACHER_DISTILL_REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "promotion_allowed": False,
        "workflow": "rtlcoder_teacher_answer",
        "errors": errors,
        "warnings": warnings,
    }


def print_rtlcoder_merge_text(result: dict[str, Any]) -> None:
    print(
        "RTLCoder teacher-distill draft rows merged."
        if result["ok"] else
        "RTLCoder teacher-distill draft row merge failed."
    )
    print()
    print(f"Tasks: {result['tasks']}")
    print(f"Answers: {result['answers']}")
    print(f"Output: {result['output']}")
    print(f"Rows written: {result['rows_written']}")
    print(f"Review status: {result['review_status']}")
    print(f"Approval status: {result['approval_status']}")
    print(f"Promotion allowed: {str(result['promotion_allowed']).lower()}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
