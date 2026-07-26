"""CLI for public/private RTL generation task preparation export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_preparation import export_generation_normalization_batches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export teacher-visible RTL generation normalization batches.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--private-output-dir", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = export_generation_normalization_batches(
        args.input, args.output_dir, args.private_output_dir,
        batch_size=args.batch_size, limit=args.limit, start_index=args.start_index, force=args.force,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"exported_rows={result.get('exported_rows', 0)} ok={result.get('ok', False)}")
        for error in result.get("errors", []): print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
