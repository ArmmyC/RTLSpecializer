#!/usr/bin/env python3
"""Export teacher-distill chat rows into model-agnostic evaluation prompt JSONL."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.release import canonical_json
from scripts.dataset.validation import validate_dataset_file


def _validate_row(row: dict[str, Any], expected_split: str) -> list[str]:
    errors: list[str] = []
    source_id = row.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        errors.append("row must contain non-empty source_id")
    if row.get("split") != expected_split:
        errors.append(f"row split {row.get('split')!r} does not match requested split {expected_split!r}")
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) != 3:
        errors.append("row must contain exactly three chat messages")
        return errors
    roles = [message.get("role") if isinstance(message, dict) else None for message in messages]
    if roles != ["system", "user", "assistant"]:
        errors.append("row messages must be in system/user/assistant order")
        return errors
    task = messages[1].get("content")
    answer = messages[2].get("content")
    if not isinstance(task, dict) or task.get("schema_version") != "rtl_task_v0.1":
        errors.append("user content must be rtl_task_v0.1")
    if not isinstance(answer, dict) or answer.get("schema_version") != "rtl_answer_v0.1":
        errors.append("assistant content must be rtl_answer_v0.1")
    return errors


def _export_prompt_row(row: dict[str, Any]) -> dict[str, Any]:
    system_prompt = row["messages"][0]["content"]
    task = deepcopy(row["messages"][1]["content"])
    answer = deepcopy(row["messages"][2]["content"])
    user_prompt = canonical_json(task)
    prompt_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    scoring_row = deepcopy(row)
    scoring_row["messages"] = [
        deepcopy(row["messages"][0]),
        {"role": "user", "content": deepcopy(task)},
    ]
    return {
        "source_id": row["source_id"],
        "split": row["split"],
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "user_content": task,
        "prompt_messages": prompt_messages,
        "expected_answer": answer,
        "scoring_row": scoring_row,
        "metadata": {
            "row_id": row.get("id"),
            "dataset_name": row.get("dataset_name"),
            "dataset_stage": row.get("dataset_stage"),
            "task_family": row.get("task_family"),
            "design_family": row.get("design_family"),
            "review_status": row.get("review_status"),
            "approval_status": row.get("approval_status"),
            "source": row.get("source"),
            "source_family": row.get("source_family"),
        },
    }


def export_rtl_eval_prompts(
    input_path: Path,
    output_path: Path,
    split: str,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    report = validate_dataset_file(input_path, strict=True)
    if not report.ok:
        errors.extend(item.format() for item in report.errors + report.warnings)
        return {
            "ok": False,
            "input_rows": 0,
            "exported_rows": 0,
            "split": split,
            "output": str(output_path),
            "errors": errors,
            "warnings": warnings,
        }, 1
    loaded, problems = load_jsonl(input_path)
    errors.extend(problem.message for problem in problems)
    rows = [row for _, row in loaded]
    exported: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        row_errors = _validate_row(row, split)
        if row_errors:
            errors.extend(f"row {index}: {message}" for message in row_errors)
            continue
        exported.append(_export_prompt_row(row))
    if not errors:
        write_jsonl(output_path, exported)
    ok = not errors and (not strict or not warnings)
    return {
        "ok": ok,
        "input_rows": len(rows),
        "exported_rows": len(exported),
        "split": split,
        "output": str(output_path),
        "errors": errors,
        "warnings": warnings,
    }, 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split", required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = export_rtl_eval_prompts(
        input_path=args.input,
        output_path=args.output,
        split=args.split,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("RTL eval prompts exported." if result["ok"] else "RTL eval prompt export failed.")
        print(f"Input rows: {result['input_rows']}")
        print(f"Exported rows: {result['exported_rows']}")
        print(f"Split: {result['split']}")
        print(f"Output: {result['output']}")
        if result["errors"]:
            print("Errors:")
            for item in result["errors"]:
                print(f"- {item}")
        if result["warnings"]:
            print("Warnings:")
            for item in result["warnings"]:
                print(f"- {item}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
