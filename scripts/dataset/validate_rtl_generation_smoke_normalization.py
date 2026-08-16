#!/usr/bin/env python3
"""Validate one untouched five-row normalization response."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_smoke_run import validate_normalization_response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-batch", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--private-assets", required=True, type=Path)
    args = parser.parse_args(argv)
    result, code = validate_normalization_response(
        raw_batch_path=args.raw_batch,
        response_path=args.response,
        private_assets_path=args.private_assets,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
