#!/usr/bin/env python3
"""CLI for canonical manual RTL run validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace_layout import validate_manual_rtl_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a canonical manual RTL teacher run.")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = validate_manual_rtl_run(args.run_root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    elif code:
        print("invalid manual RTL run")
    else:
        print("valid manual RTL run")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
