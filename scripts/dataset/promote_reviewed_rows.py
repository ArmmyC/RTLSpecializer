#!/usr/bin/env python3
"""Promote edited public draft rows into validated dataset candidates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.review_promotion import PromotionConfig, load_rows, promote_rows


def _print_text(result: dict) -> None:
    if result["ok"]:
        print("Promotion completed.")
    else:
        print("Promotion failed.")
    print()
    print(f"Input rows: {result['input_rows']}")
    print(f"Accepted rows: {result['accepted_rows']}")
    print(f"Rejected rows: {result['rejected_rows']}")
    print(f"Output: {result['output']}")
    print(f"Rejected output: {result['rejected_output']}")
    print(f"Report: {result['report']}")
    if result["errors"]:
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print()
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--target-status", choices=["validated", "reviewed"], default="validated")
    parser.add_argument("--allow-stub-answer", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows, errors = load_rows(args.input)
    if errors:
        result = {
            "ok": False,
            "input_rows": len(rows),
            "accepted_rows": 0,
            "rejected_rows": 0,
            "by_source": {},
            "by_task_type": {},
            "by_design_family": {},
            "rejection_reasons": {},
            "output": str(args.output),
            "rejected_output": str(args.output.with_name(args.output.stem + ".rejected.jsonl")),
            "report": str(args.report),
            "errors": errors,
            "warnings": [],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_text(result)
        return 1
    result, code = promote_rows(
        rows,
        args.output,
        args.report,
        PromotionConfig(args.target_status, args.allow_stub_answer, args.strict),
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
