#!/usr/bin/env python3
"""Prepare a teacher-distilled pilot fine-tuning dataset from clean task/answer JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.teacher_distill import (
    prepare_teacher_distill_dataset,
    print_prepare_text,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--train-size", required=True, type=int)
    parser.add_argument("--validation-size", required=True, type=int)
    parser.add_argument("--test-size", required=True, type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = prepare_teacher_distill_dataset(
        tasks_path=args.tasks,
        answers_path=args.answers,
        output_dir=args.output_dir,
        train_size=args.train_size,
        validation_size=args.validation_size,
        test_size=args.test_size,
        seed=args.seed,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_prepare_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
