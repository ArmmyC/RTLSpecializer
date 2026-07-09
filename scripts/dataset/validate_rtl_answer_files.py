#!/usr/bin/env python3
"""Validate standalone rtl_answer.v0.1 teacher-answer files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_answer_file_audit import add_common_args, print_summary, validate_answer_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args(argv)

    result, code = validate_answer_files(
        inputs=args.input,
        input_dirs=args.input_dir,
        glob_pattern=args.glob,
        tasks_path=args.tasks,
        output_md=args.output_md,
        output_json=args.output_json,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_summary(result, "validation")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
