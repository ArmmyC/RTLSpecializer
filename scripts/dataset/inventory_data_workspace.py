#!/usr/bin/env python3
"""Inventory the local data workspace without modifying existing data files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace import (
    DEFAULT_INVENTORY_JSON,
    DEFAULT_INVENTORY_MD,
    collect_data_workspace_inventory,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-md", type=Path, default=DEFAULT_INVENTORY_MD)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_INVENTORY_JSON)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result, code = collect_data_workspace_inventory(
        data_dir=args.data_dir,
        output_md=args.output_md,
        output_json=args.output_json,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Data workspace inventory complete.")
        print()
        print(f"Files scanned: {result['files_scanned']}")
        print(f"Detected task files: {result['task_file_count']}")
        print(f"Detected answer files: {result['answer_file_count']}")
        print(f"Detected reports: {result['report_file_count']}")
        print(f"Duplicate files: {result['duplicate_file_count']}")
        print(f"Unknown files: {result['unknown_file_count']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
