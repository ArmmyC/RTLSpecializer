#!/usr/bin/env python3
"""Produce a read-only triage report for a selected/reviewed dataset batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.review_triage import triage_review_batch, write_triage_reports


def _load(path: Path, label: str) -> tuple[list[dict], list[dict[str, str]]]:
    rows, problems = load_jsonl(path)
    issues = [
        {
            "severity": "critical",
            "code": f"{label}_input_error",
            "message": f"{label} input: {problem.message}" + (f" (line {problem.line})" if problem.line else ""),
            "suggested_action": "Fix the input JSONL file, then rerun triage.",
        }
        for problem in problems
    ]
    return [row for _, row in rows], issues


def _print_text(result: dict, output_json: Path | None, output_md: Path | None) -> None:
    print("Review batch triage completed." if result["ok"] else "Review batch triage found input errors.")
    print()
    for label, key in (
        ("Selected rows", "selected_rows"),
        ("Reviewed rows", "reviewed_rows"),
        ("Critical issues", "critical_count"),
        ("Important issues", "important_count"),
        ("Minor issues", "minor_count"),
    ):
        print(f"{label}: {result[key]}")
    if output_json:
        print(f"Output JSON: {output_json}")
    if output_md:
        print(f"Output Markdown: {output_md}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", required=True, type=Path)
    parser.add_argument("--reviewed", required=True, type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    selected_rows, selected_issues = _load(args.selected, "selected")
    reviewed_rows, reviewed_issues = _load(args.reviewed, "reviewed")
    result = triage_review_batch(selected_rows, reviewed_rows, file_issues=selected_issues + reviewed_issues)
    write_triage_reports(result, args.output_json, args.output_md)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result, args.output_json, args.output_md)
    if not result["ok"] or result["duplicate_selected_ids"] or result["duplicate_reviewed_ids"]:
        return 1
    if args.strict and (result["critical_count"] or result["important_count"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
