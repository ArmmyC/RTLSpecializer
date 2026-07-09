#!/usr/bin/env python3
"""Export a canonical teacher-distill dataset copy for fine-tuning."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
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
from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.release import file_sha256


SPLIT_FILES = ("train.jsonl", "validation.jsonl", "test.jsonl")
MANIFEST_NAME = "manifest.json"


def _is_golden_path(path: Path) -> bool:
    return any(part.lower() == "golden" for part in path.parts)


def _output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "train": output_dir / "train.jsonl",
        "validation": output_dir / "validation.jsonl",
        "test": output_dir / "test.jsonl",
        "manifest": output_dir / MANIFEST_NAME,
    }


def _validate_output_dir(output_dir: Path, *, force: bool) -> list[str]:
    errors: list[str] = []
    if _is_golden_path(output_dir):
        errors.append("--output-dir must not write into data/golden")
    if output_dir.exists() and output_dir.is_symlink():
        errors.append(f"--output-dir must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        errors.append(f"--output-dir exists and is not a directory: {output_dir}")
    output_paths = _output_paths(output_dir)
    existing_managed = [path for path in output_paths.values() if path.exists()]
    for path in existing_managed:
        if path.is_symlink():
            errors.append(f"refusing to overwrite symlinked managed output file: {path}")
    if existing_managed and not force:
        errors.append(
            "--output-dir already contains managed export files; rerun with --force to replace only "
            "train.jsonl, validation.jsonl, test.jsonl, and manifest.json"
        )
    return errors


def _load_split_rows(dataset_dir: Path) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    splits: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    if not dataset_dir.exists():
        return {}, [f"dataset directory not found: {dataset_dir}"]
    if not dataset_dir.is_dir():
        return {}, [f"dataset directory must be a directory: {dataset_dir}"]

    for split_name in SPLIT_FILES:
        split_path = dataset_dir / split_name
        if not split_path.exists():
            errors.append(f"missing split file: {split_path}")
            continue
        loaded, problems = load_jsonl(split_path)
        errors.extend(f"{split_path}:{problem.line or ''}: {problem.message}" for problem in problems)
        splits[split_name.removesuffix(".jsonl")] = [row for _, row in loaded]
    return splits, errors


def _canonicalize_row(
    row: dict[str, Any],
    *,
    split_name: str,
    row_index: int,
    golden_dataset: bool,
    normalized_alias_counts: dict[str, Counter[str]],
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    errors: list[str] = []
    updated = deepcopy(row)
    row_id = updated.get("id") if isinstance(updated.get("id"), str) else f"{split_name}:{row_index}"
    if updated.get("approval_status") == "approved" and not golden_dataset:
        errors.append(f"{split_name} row {row_id} is approved outside a golden dataset")
        return None, False, errors
    messages = updated.get("messages")
    if not isinstance(messages, list) or len(messages) != 3:
        errors.append(f"{split_name} row {row_id} must contain exactly three messages")
        return None, False, errors

    expected_roles = ("system", "user", "assistant")
    for index, expected_role in enumerate(expected_roles):
        message = messages[index]
        if not isinstance(message, dict):
            errors.append(f"{split_name} row {row_id} message {index} must be an object")
            return None, False, errors
        if message.get("role") != expected_role:
            errors.append(f"{split_name} row {row_id} message {index} role must be {expected_role!r}")
            return None, False, errors

    user_content = messages[1].get("content")
    assistant_content = messages[2].get("content")
    if not isinstance(user_content, dict):
        errors.append(f"{split_name} row {row_id} user content must be an object")
        return None, False, errors
    if not isinstance(assistant_content, dict):
        errors.append(f"{split_name} row {row_id} assistant content must be an object")
        return None, False, errors

    changed = False
    user_schema = user_content.get("schema_version")
    assistant_schema = assistant_content.get("schema_version")
    if user_schema not in TASK_SCHEMA_VERSIONS:
        errors.append(f"{split_name} row {row_id} user schema_version must be {TASK_SCHEMA_VERSION!r}")
    elif user_schema != TASK_SCHEMA_VERSION:
        normalized_alias_counts["user"][str(user_schema)] += 1
        user_content["schema_version"] = TASK_SCHEMA_VERSION
        changed = True

    if assistant_schema not in ANSWER_SCHEMA_VERSIONS:
        errors.append(f"{split_name} row {row_id} assistant schema_version must be {ANSWER_SCHEMA_VERSION!r}")
    elif assistant_schema != ANSWER_SCHEMA_VERSION:
        normalized_alias_counts["assistant"][str(assistant_schema)] += 1
        assistant_content["schema_version"] = ANSWER_SCHEMA_VERSION
        changed = True

    return updated, changed, errors


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def export_canonical_finetune_dataset(
    dataset_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    errors.extend(_validate_output_dir(output_dir, force=force))
    split_rows, load_errors = _load_split_rows(dataset_dir)
    errors.extend(load_errors)
    golden_dataset = _is_golden_path(dataset_dir)

    normalized_alias_counts = {
        "user": Counter(),
        "assistant": Counter(),
    }
    canonical_splits: dict[str, list[dict[str, Any]]] = {}
    changed_rows = 0
    total_rows = 0

    for split_name in ("train", "validation", "test"):
        rows = split_rows.get(split_name, [])
        canonical_rows: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows, 1):
            total_rows += 1
            canonical_row, changed, row_errors = _canonicalize_row(
                row,
                split_name=split_name,
                row_index=row_index,
                golden_dataset=golden_dataset,
                normalized_alias_counts=normalized_alias_counts,
            )
            errors.extend(row_errors)
            if canonical_row is not None:
                canonical_rows.append(canonical_row)
            if changed:
                changed_rows += 1
        canonical_splits[split_name] = canonical_rows

    if errors:
        return {
            "ok": False,
            "dataset_dir": str(dataset_dir),
            "output_dir": str(output_dir),
            "total_rows": total_rows,
            "split_counts": {name: len(rows) for name, rows in canonical_splits.items()},
            "changed_rows": changed_rows,
            "canonical_schema_versions": {
                "user": TASK_SCHEMA_VERSION,
                "assistant": ANSWER_SCHEMA_VERSION,
            },
            "normalized_alias_counts": {
                "user": dict(sorted(normalized_alias_counts["user"].items())),
                "assistant": dict(sorted(normalized_alias_counts["assistant"].items())),
            },
            "output_files": {},
            "errors": errors,
            "warnings": [],
        }, 1

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = _output_paths(output_dir)
    for split_name in ("train", "validation", "test"):
        write_jsonl(output_paths[split_name], canonical_splits[split_name])

    manifest = {
        "created_by_script": "scripts/finetune/export_canonical_finetune_dataset.py",
        "dataset_dir": str(dataset_dir),
        "output_dir": str(output_dir),
        "canonical_schema_versions": {
            "user": TASK_SCHEMA_VERSION,
            "assistant": ANSWER_SCHEMA_VERSION,
        },
        "split_counts": {name: len(rows) for name, rows in canonical_splits.items()},
        "total_rows": total_rows,
        "changed_rows": changed_rows,
        "normalized_alias_counts": {
            "user": dict(sorted(normalized_alias_counts["user"].items())),
            "assistant": dict(sorted(normalized_alias_counts["assistant"].items())),
        },
        "input_files": {
            split_name: {
                "path": str(dataset_dir / f"{split_name}.jsonl"),
                "sha256": file_sha256(dataset_dir / f"{split_name}.jsonl"),
            }
            for split_name in ("train", "validation", "test")
        },
        "output_files": {
            split_name: {
                "path": str(output_paths[split_name]),
                "sha256": file_sha256(output_paths[split_name]),
            }
            for split_name in ("train", "validation", "test")
        },
    }
    manifest["output_files"]["manifest"] = {
        "path": str(output_paths["manifest"]),
        "sha256": None,
        "note": "Manifest self-hash is omitted because embedding it would change the file bytes.",
    }
    _write_json(output_paths["manifest"], manifest)

    result = {
        "ok": True,
        "dataset_dir": str(dataset_dir),
        "output_dir": str(output_dir),
        "total_rows": total_rows,
        "split_counts": {name: len(rows) for name, rows in canonical_splits.items()},
        "changed_rows": changed_rows,
        "canonical_schema_versions": {
            "user": TASK_SCHEMA_VERSION,
            "assistant": ANSWER_SCHEMA_VERSION,
        },
        "normalized_alias_counts": {
            "user": dict(sorted(normalized_alias_counts["user"].items())),
            "assistant": dict(sorted(normalized_alias_counts["assistant"].items())),
        },
        "output_files": {name: str(path) for name, path in output_paths.items()},
        "errors": [],
        "warnings": [],
    }
    return result, 0


def _print_text(result: dict[str, Any]) -> None:
    print("Canonical fine-tune dataset export passed." if result["ok"] else "Canonical fine-tune dataset export failed.")
    print(f"Dataset dir: {result['dataset_dir']}")
    print(f"Output dir: {result['output_dir']}")
    print(f"Total rows: {result['total_rows']}")
    print(f"Changed rows: {result['changed_rows']}")
    for split_name, count in result["split_counts"].items():
        print(f"{split_name}: {count}")
    if result["normalized_alias_counts"]["user"] or result["normalized_alias_counts"]["assistant"]:
        print("Normalized aliases:")
        if result["normalized_alias_counts"]["user"]:
            print(f"- user: {json.dumps(result['normalized_alias_counts']['user'], sort_keys=True)}")
        if result["normalized_alias_counts"]["assistant"]:
            print(f"- assistant: {json.dumps(result['normalized_alias_counts']['assistant'], sort_keys=True)}")
    if result["errors"]:
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = export_canonical_finetune_dataset(
        args.dataset_dir,
        args.output_dir,
        force=args.force,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
