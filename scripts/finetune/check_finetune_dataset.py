#!/usr/bin/env python3
"""Inspect a teacher-distill dataset before fine-tuning."""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.constants import (
    ANSWER_SCHEMA_VERSION,
    ANSWER_SCHEMA_VERSIONS,
    TASK_SCHEMA_VERSION,
    TASK_SCHEMA_VERSIONS,
)
from scripts.dataset.io_utils import load_jsonl


SPLIT_FILES = ("train.jsonl", "validation.jsonl", "test.jsonl")


def _message_char_length(message: dict[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, ensure_ascii=False, sort_keys=True))


def _is_golden_dataset(dataset_dir: Path) -> bool:
    return any(part.lower() == "golden" for part in dataset_dir.parts)


def _increment_missing(counter: Counter[str], key: str) -> None:
    counter[key] += 1


def _count_schema_alias(aliases: dict[str, Counter[str]], role: str, version: str) -> None:
    aliases[role][version] += 1


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Fine-tune dataset check",
        "",
        f"- Dataset dir: `{summary['dataset_dir']}`",
        f"- OK: `{str(summary['ok']).lower()}`",
        f"- Total rows: {summary['total_rows']}",
        "",
        "## Row counts",
        "",
    ]
    for split_name, split_result in summary["splits"].items():
        lines.append(
            f"- `{split_name}`: {split_result['rows']} rows, "
            f"mean chars {split_result['approx_char_lengths']['mean']}, "
            f"max chars {split_result['approx_char_lengths']['max']}"
        )
    lines.extend([
        "",
        "## Distributions",
        "",
        f"- Source: `{json.dumps(summary['source_distribution'], sort_keys=True)}`",
        f"- Review status: `{json.dumps(summary['review_status_distribution'], sort_keys=True)}`",
        f"- Approval status: `{json.dumps(summary['approval_status_distribution'], sort_keys=True)}`",
        "",
        "## Schema aliases",
        "",
        f"- User/task aliases: `{json.dumps(summary['schema_aliases']['user'], sort_keys=True)}`",
        f"- Assistant/answer aliases: `{json.dumps(summary['schema_aliases']['assistant'], sort_keys=True)}`",
        "",
        "## Missing fields",
        "",
        f"- `{json.dumps(summary['missing_fields'], sort_keys=True)}`",
        "",
        "## Errors",
        "",
    ])
    if summary["errors"]:
        lines.extend(f"- {error}" for error in summary["errors"])
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if summary["warnings"]:
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def check_finetune_dataset(
    dataset_dir: Path,
    *,
    output_md: Path | None = None,
    output_json: Path | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    split_summaries: dict[str, dict[str, Any]] = {}
    missing_fields: Counter[str] = Counter()
    source_distribution: Counter[str] = Counter()
    review_status_distribution: Counter[str] = Counter()
    approval_status_distribution: Counter[str] = Counter()
    schema_aliases: dict[str, Counter[str]] = {
        "user": Counter(),
        "assistant": Counter(),
    }
    total_rows = 0
    total_char_lengths: list[int] = []
    golden_dataset = _is_golden_dataset(dataset_dir)

    if not dataset_dir.exists():
        errors.append(f"dataset directory not found: {dataset_dir}")
    elif not dataset_dir.is_dir():
        errors.append(f"dataset directory must be a directory: {dataset_dir}")
    if errors:
        summary = {
            "ok": False,
            "dataset_dir": str(dataset_dir),
            "splits": {},
            "total_rows": 0,
            "approx_char_lengths": {"mean": 0.0, "max": 0},
            "missing_fields": {},
            "schema_aliases": {"user": {}, "assistant": {}},
            "source_distribution": {},
            "review_status_distribution": {},
            "approval_status_distribution": {},
            "errors": errors,
            "warnings": warnings,
        }
        if output_json is not None:
            output_json.parent.mkdir(parents=True, exist_ok=True)
            output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if output_md is not None:
            output_md.parent.mkdir(parents=True, exist_ok=True)
            output_md.write_text(_render_markdown(summary), encoding="utf-8")
        return summary, 1

    for split_name in SPLIT_FILES:
        split_path = dataset_dir / split_name
        if not split_path.exists():
            errors.append(f"missing split file: {split_path}")
            continue
        loaded, problems = load_jsonl(split_path)
        errors.extend(f"{split_path}: {problem.message}" for problem in problems)
        split_char_lengths: list[int] = []
        for line_number, row in loaded:
            total_rows += 1
            row_id = row.get("id") if isinstance(row.get("id"), str) else f"line_{line_number}"
            source_distribution[str(row.get("source", "<missing>"))] += 1
            review_status_distribution[str(row.get("review_status", "<missing>"))] += 1
            approval_status_distribution[str(row.get("approval_status", "<missing>"))] += 1
            if row.get("approval_status") == "approved" and not golden_dataset:
                errors.append(f"{split_path}:{line_number} row {row_id} is approved outside a golden dataset")

            messages = row.get("messages")
            if not isinstance(messages, list):
                _increment_missing(missing_fields, "messages")
                errors.append(f"{split_path}:{line_number} row {row_id} is missing messages list")
                continue
            if len(messages) != 3:
                _increment_missing(missing_fields, "messages[3_exact]")
                errors.append(f"{split_path}:{line_number} row {row_id} must contain exactly three messages")
                continue
            expected_roles = ("system", "user", "assistant")
            role_error = False
            for index, expected_role in enumerate(expected_roles):
                message = messages[index]
                if not isinstance(message, dict):
                    _increment_missing(missing_fields, f"messages[{index}]")
                    errors.append(f"{split_path}:{line_number} row {row_id} message {index} must be an object")
                    role_error = True
                    continue
                if message.get("role") != expected_role:
                    _increment_missing(missing_fields, f"messages[{index}].role")
                    errors.append(
                        f"{split_path}:{line_number} row {row_id} message {index} role must be {expected_role!r}"
                    )
                    role_error = True
                if "content" not in message:
                    _increment_missing(missing_fields, f"messages[{index}].content")
                    errors.append(f"{split_path}:{line_number} row {row_id} message {index} is missing content")
                    role_error = True
            if role_error:
                continue

            system_content = messages[0].get("content")
            user_content = messages[1].get("content")
            assistant_content = messages[2].get("content")
            if not isinstance(system_content, str) or not system_content:
                _increment_missing(missing_fields, "messages[0].content")
                errors.append(f"{split_path}:{line_number} row {row_id} system content must be a non-empty string")
                continue
            if not isinstance(user_content, dict):
                _increment_missing(missing_fields, "messages[1].content")
                errors.append(f"{split_path}:{line_number} row {row_id} user content must be an object")
                continue
            if not isinstance(assistant_content, dict):
                _increment_missing(missing_fields, "messages[2].content")
                errors.append(f"{split_path}:{line_number} row {row_id} assistant content must be an object")
                continue

            user_schema = user_content.get("schema_version")
            assistant_schema = assistant_content.get("schema_version")
            if user_schema not in TASK_SCHEMA_VERSIONS:
                _increment_missing(missing_fields, "messages[1].content.schema_version")
                errors.append(
                    f"{split_path}:{line_number} row {row_id} user schema_version must be {TASK_SCHEMA_VERSION!r}"
                )
            elif user_schema != TASK_SCHEMA_VERSION:
                _count_schema_alias(schema_aliases, "user", str(user_schema))
            if assistant_schema not in ANSWER_SCHEMA_VERSIONS:
                _increment_missing(missing_fields, "messages[2].content.schema_version")
                errors.append(
                    f"{split_path}:{line_number} row {row_id} assistant schema_version must be {ANSWER_SCHEMA_VERSION!r}"
                )
            elif assistant_schema != ANSWER_SCHEMA_VERSION:
                _count_schema_alias(schema_aliases, "assistant", str(assistant_schema))

            split_row_char_length = sum(_message_char_length(message) for message in messages)
            split_char_lengths.append(split_row_char_length)
            total_char_lengths.append(split_row_char_length)

        split_summaries[split_name.removesuffix(".jsonl")] = {
            "path": str(split_path),
            "rows": len(loaded),
            "approx_char_lengths": {
                "mean": round(sum(split_char_lengths) / len(split_char_lengths), 2) if split_char_lengths else 0.0,
                "max": max(split_char_lengths) if split_char_lengths else 0,
            },
        }

    if schema_aliases["assistant"]:
        warnings.append("assistant schema aliases are present; this is acceptable for teacher-distill pilot data")
    if schema_aliases["user"]:
        warnings.append("user schema aliases are present")

    summary = {
        "ok": not errors,
        "dataset_dir": str(dataset_dir),
        "splits": split_summaries,
        "total_rows": total_rows,
        "approx_char_lengths": {
            "mean": round(sum(total_char_lengths) / len(total_char_lengths), 2) if total_char_lengths else 0.0,
            "max": max(total_char_lengths) if total_char_lengths else 0,
        },
        "missing_fields": dict(sorted(missing_fields.items())),
        "schema_aliases": {
            "user": dict(sorted(schema_aliases["user"].items())),
            "assistant": dict(sorted(schema_aliases["assistant"].items())),
        },
        "source_distribution": dict(sorted(source_distribution.items())),
        "review_status_distribution": dict(sorted(review_status_distribution.items())),
        "approval_status_distribution": dict(sorted(approval_status_distribution.items())),
        "errors": errors,
        "warnings": warnings,
    }
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if output_md is not None:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_render_markdown(summary), encoding="utf-8")
    return summary, 0 if summary["ok"] else 1


def _print_text(summary: dict[str, Any]) -> None:
    print("Fine-tune dataset check passed." if summary["ok"] else "Fine-tune dataset check failed.")
    print(f"Dataset dir: {summary['dataset_dir']}")
    print(f"Total rows: {summary['total_rows']}")
    for split_name, split_result in summary["splits"].items():
        print(
            f"{split_name}: {split_result['rows']} rows, "
            f"mean chars {split_result['approx_char_lengths']['mean']}, "
            f"max chars {split_result['approx_char_lengths']['max']}"
        )
    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")
    if summary["errors"]:
        print("Errors:")
        for error in summary["errors"]:
            print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary, code = check_finetune_dataset(
        args.dataset_dir,
        output_md=args.output_md,
        output_json=args.output_json,
    )
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_text(summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
