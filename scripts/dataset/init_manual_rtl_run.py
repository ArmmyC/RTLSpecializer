#!/usr/bin/env python3
"""CLI for atomic manual RTL run initialization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace_layout import WorkspaceError, initialize_manual_rtl_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize a canonical manual RTL teacher run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-dataset", required=True)
    parser.add_argument("--runs-root", type=Path, default=Path("data/runs/manual_rtl_teacher"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = initialize_manual_rtl_run(args.run_id, args.source_dataset, args.runs_root, resume=args.resume)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"{result['status']}: {result['run_id']}")
        return 0
    except (WorkspaceError, OSError) as exc:
        error = {"ok": False, "errors": [str(exc)], "warnings": []}
        if args.json:
            print(json.dumps(error, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
