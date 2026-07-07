#!/usr/bin/env python3
"""Validate returned rtl_answer_v0.1 teacher-answer batches."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_answer_teacher_batches import (
    print_validation_text,
    validate_rtl_answer_teacher_batch,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = validate_rtl_answer_teacher_batch(
        tasks_path=args.tasks,
        answers_path=args.answers,
        output_md=args.output_md,
        output_json=args.output_json,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_validation_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
