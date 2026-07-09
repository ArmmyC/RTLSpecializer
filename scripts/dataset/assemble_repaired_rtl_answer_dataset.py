#!/usr/bin/env python3
"""Assemble repaired standalone rtl_answer.v0.1 files into one deterministic JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_answer_dataset_assembly import (
    DEFAULT_PRIORITY_ORDER,
    assemble_repaired_rtl_answer_dataset,
)
from scripts.dataset.rtl_answer_file_audit import DEFAULT_GLOB


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers-dir", required=True, type=Path)
    parser.add_argument("--answers-glob", default=DEFAULT_GLOB)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report-md", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--priority", default=",".join(DEFAULT_PRIORITY_ORDER))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = assemble_repaired_rtl_answer_dataset(
        answers_dir=args.answers_dir,
        answers_glob=args.answers_glob,
        tasks_path=args.tasks,
        output_path=args.output,
        report_md=args.report_md,
        report_json=args.report_json,
        priority=args.priority,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("RTL answer dataset assembly passed." if result["ok"] else "RTL answer dataset assembly found issues.")
        print()
        print(f"Files scanned: {result['files_scanned']}")
        print(f"Answers scanned: {result['answers_scanned']}")
        print(f"Selected answers: {result['selected_answers']}")
        print(f"Validation errors: {result['validation_error_count']}")
        print(f"Validation warnings: {result['validation_warning_count']}")
        print(f"Manual-review flags: {result['manual_review_flag_count']}")
        print(f"Output: {result['output_path']}")
        print(f"SHA256: {result['output_sha256'] or 'not_written'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
