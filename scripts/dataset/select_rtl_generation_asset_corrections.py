"""Select the bounded train-only RTL verification asset correction set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_asset_corrections import (
    EXPECTED_SOURCE_IDS,
    select_correction_rows,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--source-id",
        dest="source_ids",
        action="append",
        help="Explicit train source ID; defaults to the bounded v002 set.",
    )
    args = parser.parse_args(argv)
    result, code = select_correction_rows(
        args.inventory,
        args.split_manifest,
        args.output,
        source_ids=args.source_ids or EXPECTED_SOURCE_IDS,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
