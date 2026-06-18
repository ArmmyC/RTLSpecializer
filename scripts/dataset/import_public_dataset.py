#!/usr/bin/env python3
"""Import local public RTL artifacts into conservative dataset_v0.1 draft rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.adapters import ImportOptions, ImportRejection, get_adapter
from scripts.dataset.draft_rows import build_draft_row
from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.validation import validate_dataset_file


def rejected_path_for(output: Path) -> Path:
    return output.with_name(output.stem + ".rejected.jsonl")


def _validate_rows(rows: list[dict[str, Any]], output: Path) -> list[str]:
    temp = output.with_name(output.name + ".validation.tmp.jsonl")
    try:
        write_jsonl(temp, rows)
        report = validate_dataset_file(temp, strict=True)
        return [item.format() for item in report.errors + report.warnings]
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _rejection_row(rejection: ImportRejection) -> dict[str, Any]:
    return {
        "source_id": rejection.source_id,
        "reason": rejection.reason,
        "errors": rejection.errors,
        "metadata": rejection.metadata or {},
    }


def import_public_dataset(
    adapter_name: str,
    input_path: Path,
    output_path: Path,
    options: ImportOptions,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        adapter = get_adapter(adapter_name)
    except ValueError as exc:
        result = {
            "ok": False, "adapter": adapter_name, "discovered_examples": 0,
            "imported_rows": 0, "rejected_examples": 0, "output": str(output_path),
            "rejected_output": str(rejected_path_for(output_path)), "errors": [str(exc)], "warnings": [],
        }
        return result, 1

    discovery = adapter.discover_examples(input_path, options)
    warnings.extend(discovery.warnings)
    accepted: list[dict[str, Any]] = []
    rejected = [_rejection_row(item) for item in discovery.rejections]
    seen_ids: set[str] = set()
    for example in discovery.examples:
        row = build_draft_row(example)
        row_id = row["id"]
        if row_id in seen_ids:
            rejected.append({
                "source_id": example.source_id,
                "reason": "duplicate output row id",
                "errors": [f"duplicate output row id: {row_id}"],
                "metadata": example.metadata,
            })
            continue
        row_errors = _validate_rows([row], output_path)
        if row_errors:
            rejected.append({
                "source_id": example.source_id,
                "reason": "generated row failed validation",
                "errors": row_errors,
                "metadata": example.metadata,
            })
            continue
        seen_ids.add(row_id)
        accepted.append(row)

    if accepted:
        whole_errors = _validate_rows(accepted, output_path)
        if whole_errors:
            errors.extend(whole_errors)
            accepted = []

    rejected_path = rejected_path_for(output_path)
    write_jsonl(output_path, accepted)
    write_jsonl(rejected_path, rejected)

    if not accepted and not errors:
        errors.append("no rows imported")
    ok = bool(accepted) and not errors and not (strict and rejected)
    result = {
        "ok": ok,
        "adapter": adapter_name,
        "discovered_examples": discovery.discovered_examples,
        "imported_rows": len(accepted),
        "rejected_examples": len(rejected),
        "output": str(output_path),
        "rejected_output": str(rejected_path),
        "errors": errors,
        "warnings": warnings,
    }
    return result, 0 if ok or (accepted and not strict and not errors) else 1


def _print_text(result: dict[str, Any]) -> None:
    if result["ok"]:
        title = "Public dataset import completed."
    elif result["imported_rows"] > 0:
        title = "Public dataset import completed with rejections."
    else:
        title = "Public dataset import failed."
    print(title)
    print()
    print(f"Adapter: {result['adapter']}")
    print(f"Discovered examples: {result['discovered_examples']}")
    print(f"Imported rows: {result['imported_rows']}")
    print(f"Rejected examples: {result['rejected_examples']}")
    print(f"Output: {result['output']}")
    print(f"Rejected output: {result['rejected_output']}")
    if result["errors"]:
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print()
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source")
    parser.add_argument("--license")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--allow-absolute-paths", action="store_true")
    parser.add_argument("--allow-outside-root", action="store_true")
    parser.add_argument("--max-artifact-bytes", type=int, default=1_048_576)
    args = parser.parse_args(argv)
    options = ImportOptions(
        source=args.source,
        license=args.license,
        limit=args.limit,
        allow_absolute_paths=args.allow_absolute_paths,
        allow_outside_root=args.allow_outside_root,
        max_artifact_bytes=args.max_artifact_bytes,
    )
    result, exit_code = import_public_dataset(args.adapter, args.input, args.output, options, args.strict)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
