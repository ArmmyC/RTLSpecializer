#!/usr/bin/env python3
"""Merge RTLCoder tasks and teacher answers into unreviewed teacher-distill draft rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtlcoder_teacher_answers import (
    merge_rtlcoder_teacher_distill_rows,
    print_rtlcoder_merge_text,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--system-prompt", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = merge_rtlcoder_teacher_distill_rows(
        tasks_path=args.tasks,
        answers_path=args.answers,
        output_path=args.output,
        system_prompt_path=args.system_prompt,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_rtlcoder_merge_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
