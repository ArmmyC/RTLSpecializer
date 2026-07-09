#!/usr/bin/env python3
"""Create a safe move plan for organizing the local data workspace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace import (
    DEFAULT_INVENTORY_JSON,
    DEFAULT_PLAN_JSON,
    DEFAULT_PLAN_MD,
    plan_data_workspace_cleanup,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--inventory-json", type=Path, default=DEFAULT_INVENTORY_JSON)
    parser.add_argument("--plan-md", type=Path, default=DEFAULT_PLAN_MD)
    parser.add_argument("--plan-json", type=Path, default=DEFAULT_PLAN_JSON)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-medium-confidence", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = plan_data_workspace_cleanup(
        data_dir=args.data_dir,
        inventory_json=args.inventory_json,
        plan_md=args.plan_md,
        plan_json=args.plan_json,
        apply=args.apply,
        dry_run=args.dry_run or not args.apply,
        include_medium_confidence=args.include_medium_confidence,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Data workspace cleanup plan prepared.")
        print()
        print(f"Proposed moves: {result['proposed_move_count']}")
        print(f"High-confidence moves: {result['high_confidence_move_count']}")
        print(f"Medium-confidence moves: {result['medium_confidence_move_count']}")
        print(f"Low-confidence moves: {result['low_confidence_move_count']}")
        print(f"Manual-review moves: {result['manual_review_move_count']}")
        print(f"Applied moves: {result['applied_move_count']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
