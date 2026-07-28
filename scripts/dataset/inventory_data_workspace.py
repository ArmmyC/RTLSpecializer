#!/usr/bin/env python3
"""CLI for deterministic Data Workspace v2 inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace import (
    DEFAULT_INVENTORY_JSON,
    DEFAULT_INVENTORY_MD,
    collect_data_workspace_inventory,
)
from scripts.dataset.data_workspace_layout import WorkspaceError, inventory_data_workspace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory data/ without following symlinks.")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output", type=Path)
    # Keep the pre-v2 CLI contract available for existing operators. New v2
    # commands use --data-root/--output and never write the legacy report pair.
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--force", action="store_true", help="replace the exact requested report output")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)
    try:
        legacy_invocation = any(value is not None for value in (args.data_dir, args.output_md, args.output_json)) or (
            args.data_root is None and args.output is None and not args.force
        )
        if legacy_invocation:
            result, code = collect_data_workspace_inventory(
                data_dir=args.data_dir or Path("data"),
                output_md=args.output_md or DEFAULT_INVENTORY_MD,
                output_json=args.output_json or DEFAULT_INVENTORY_JSON,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(f"files={result['files_scanned']} unknown={result['unknown_file_count']}")
            return code
        data_root = args.data_root or Path("data")
        report = inventory_data_workspace(data_root, args.output, force=args.force)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"entries={report['summary']['entry_count']} files={report['summary']['file_count']} unknown={report['summary']['unknown_entry_count']}")
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
