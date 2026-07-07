#!/usr/bin/env python3
"""Validate a manually returned VerilogEval rtl_task_v0.1 normalization batch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.verilog_eval_normalization_batches import (
    print_validation_text,
    validate_verilog_eval_normalized_batch,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-batch", required=True, type=Path)
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = validate_verilog_eval_normalized_batch(args.raw_batch, args.normalized)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_validation_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
