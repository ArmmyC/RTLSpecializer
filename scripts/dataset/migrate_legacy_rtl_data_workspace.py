#!/usr/bin/env python3
"""CLI for non-destructive legacy RTL workspace migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace_layout import WorkspaceError, migrate_legacy_rtl_data_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply copy-only legacy RTL workspace migration.")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--run-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="plan only (the default)")
    mode.add_argument("--apply", action="store_true", help="copy after complete preflight")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true", help="replace the exact requested report output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = migrate_legacy_rtl_data_workspace(
            args.data_root,
            args.run_id,
            apply=args.apply,
            output=args.output,
            force=args.force,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"{report['mode']}: mappings={report['summary']['mapping_count']} collisions={report['summary']['collision_count']}")
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
