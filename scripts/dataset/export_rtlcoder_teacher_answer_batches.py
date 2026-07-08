#!/usr/bin/env python3
"""Export RTLCoder rtl_task_v0.1 rows into deterministic teacher-answer batches."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_answer_teacher_batches import print_export_text
from scripts.dataset.rtlcoder_teacher_answers import (
    DEFAULT_BATCH_SIZE,
    export_rtlcoder_teacher_answer_batches,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Replace only exact managed batch files created by this tool")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = export_rtlcoder_teacher_answer_batches(
        input_path=args.input,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        limit=args.limit,
        start_index=args.start_index,
        force=args.force,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_export_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
