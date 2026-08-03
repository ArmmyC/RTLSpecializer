"""CLI for validating a manually returned RTL generation task batch."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_preparation import validate_generation_normalized_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate normalized rtl_generation_task_v0.1 rows without rewriting input.")
    parser.add_argument("--raw-batch", required=True, type=Path)
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--private-assets", type=Path)
    parser.add_argument(
        "--strict-response",
        action="store_true",
        help="require returned JSON to contain only a top-level rows array",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = validate_generation_normalized_batch(
        args.raw_batch,
        args.normalized,
        args.private_assets,
        require_response_object=args.strict_response,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"rows={result.get('rows', 0)} ok={result.get('ok', False)}")
        for error in result.get("errors", []): print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
