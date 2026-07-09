#!/usr/bin/env python3
"""Safely repair standalone rtl_answer.v0.1 teacher-answer files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_answer_file_audit import add_common_args, print_summary, repair_answer_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--backup", dest="backup", action="store_true", default=True)
    parser.add_argument("--no-backup", dest="backup", action="store_false")
    parser.add_argument("--report-md", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    result, code = repair_answer_files(
        inputs=args.input,
        input_dirs=args.input_dir,
        glob_pattern=args.glob,
        output_dir=args.output_dir,
        in_place=args.in_place,
        backup=args.backup,
        tasks_path=args.tasks,
        report_md=args.report_md,
        report_json=args.report_json,
        strict=args.strict,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result, "repair")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
