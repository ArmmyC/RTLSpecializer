#!/usr/bin/env python3
"""Prepare a teacher-distilled pilot fine-tuning dataset from clean task/answer JSONL."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.teacher_distill import (
    prepare_teacher_distill_dataset,
    print_prepare_text,
)


def _parse_split_size(value: str) -> tuple[str, int | float]:
    try:
        return "count", int(value)
    except ValueError:
        try:
            return "ratio", float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid split size {value!r}") from exc


def _task_row_count(tasks_path: Path) -> int:
    rows, problems = load_jsonl(tasks_path)
    if problems:
        detail = "; ".join(f"line {problem.line}: {problem.message}" if problem.line else problem.message for problem in problems)
        raise argparse.ArgumentTypeError(f"cannot resolve ratio split sizes because the task file could not be counted: {detail}")
    return len(rows)


def _resolve_split_sizes(tasks_path: Path, train_raw: str, validation_raw: str, test_raw: str) -> tuple[int, int, int]:
    parsed = [
        ("train",) + _parse_split_size(train_raw),
        ("validation",) + _parse_split_size(validation_raw),
        ("test",) + _parse_split_size(test_raw),
    ]
    modes = {mode for _, mode, _ in parsed}
    if modes == {"count"}:
        return tuple(int(value) for _, _, value in parsed)
    if modes != {"ratio"}:
        raise argparse.ArgumentTypeError("split sizes must be either all integer counts or all fractional ratios")

    ratios = [float(value) for _, _, value in parsed]
    if any(value < 0 for value in ratios):
        raise argparse.ArgumentTypeError("ratio split sizes must be non-negative")
    if not math.isclose(sum(ratios), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise argparse.ArgumentTypeError("ratio split sizes must sum to 1.0")

    task_count = _task_row_count(tasks_path)
    exact = [ratio * task_count for ratio in ratios]
    counts = [math.floor(value) for value in exact]
    remainder = task_count - sum(counts)
    order = sorted(range(len(exact)), key=lambda index: (-(exact[index] - counts[index]), index))
    for index in order[:remainder]:
        counts[index] += 1
    return tuple(counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--train-size", required=True)
    parser.add_argument("--validation-size", "--val-size", dest="validation_size", required=True)
    parser.add_argument("--test-size", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    train_size, validation_size, test_size = _resolve_split_sizes(
        args.tasks,
        args.train_size,
        args.validation_size,
        args.test_size,
    )

    result, code = prepare_teacher_distill_dataset(
        tasks_path=args.tasks,
        answers_path=args.answers,
        output_dir=args.output_dir,
        train_size=train_size,
        validation_size=validation_size,
        test_size=test_size,
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
