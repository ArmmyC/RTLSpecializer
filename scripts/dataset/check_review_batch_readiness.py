#!/usr/bin/env python3
"""Check whether manually edited review rows are ready for promotion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.review_readiness import (
    check_review_readiness, load_review_files, write_readiness_reports,
)


def _print_text(result: dict, output_json: Path | None, output_md: Path | None) -> None:
    print("Review readiness check completed." if result["ok"] else "Review readiness check failed.")
    print()
    for label, key in (
        ("Selected rows", "selected_rows"), ("Reviewed rows", "reviewed_rows"),
        ("Ready rows", "ready_rows"), ("Needs work", "needs_work_rows"),
    ):
        print(f"{label}: {result[key]}")
    print(f"Missing rows: {len(result['missing_reviewed_rows'])}")
    print(f"Extra rows: {len(result['extra_reviewed_rows'])}")
    if output_json:
        print(f"Output JSON: {output_json}")
    if output_md:
        print(f"Output Markdown: {output_md}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", required=True, type=Path)
    parser.add_argument("--reviewed", required=True, type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    loaded = load_review_files(args.selected, args.reviewed)
    result = check_review_readiness(
        loaded.selected_rows, loaded.reviewed_rows,
        selected_validation_errors=loaded.selected_errors_by_id,
        reviewed_validation_errors=loaded.reviewed_errors_by_id,
        selected_file_errors=loaded.selected_errors,
        reviewed_file_errors=loaded.reviewed_errors,
        selected_file_warnings=loaded.selected_warnings,
        reviewed_file_warnings=loaded.reviewed_warnings,
    )
    write_readiness_reports(result, args.output_json, args.output_md)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result, args.output_json, args.output_md)
    fatal = bool(result["errors"] or result["duplicate_selected_ids"] or result["duplicate_reviewed_ids"])
    if fatal or not result["matched_rows"]:
        return 1
    if args.strict and not result["all_rows_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
