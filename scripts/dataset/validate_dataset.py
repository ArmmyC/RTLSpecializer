#!/usr/bin/env python3
"""Validate a dataset JSONL file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.validation import ValidationReport, validate_dataset_file


def _print_text(report: ValidationReport) -> None:
    if report.errors:
        state = "failed"
    elif report.warnings:
        state = "passed with warnings"
    else:
        state = "passed"
    print(f"Dataset validation {state}.\n")
    print(f"Rows: {report.rows}")
    print(f"Errors: {len(report.errors)}")
    print(f"Warnings: {len(report.warnings)}")
    if report.errors:
        print("\nErrors:")
        for item in report.errors:
            print(f"- {item.format()}")
    if report.warnings:
        print("\nWarnings:")
        for item in report.warnings:
            print(f"- {item.format()}")
    if report.summary.get("by_task_type"):
        print("\nTask types:")
        for name, count in report.summary["by_task_type"].items():
            print(f"- {name}: {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    report = validate_dataset_file(args.input, strict=args.strict)
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_text(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
