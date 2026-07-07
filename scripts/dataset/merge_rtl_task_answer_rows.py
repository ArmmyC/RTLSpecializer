#!/usr/bin/env python3
"""Merge rtl_task_v0.1 rows and returned rtl_answer_v0.1 rows into draft chat JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_answer_teacher_batches import (
    merge_rtl_task_answer_rows,
    print_merge_text,
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

    result, code = merge_rtl_task_answer_rows(
        tasks_path=args.tasks,
        answers_path=args.answers,
        output_path=args.output,
        system_prompt_path=args.system_prompt,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_merge_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
