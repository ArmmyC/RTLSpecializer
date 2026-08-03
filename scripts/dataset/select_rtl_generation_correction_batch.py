"""Select the next bounded train-only RTL verification correction batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_selection import (
    BATCH_ID,
    CORRECTION_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
    load_source_ids,
    select_batch_rows,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--source-acquisition", required=True, type=Path)
    parser.add_argument("--ids-output", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--source-ids-file", type=Path)
    parser.add_argument("--source-commit", default=SOURCE_COMMIT)
    parser.add_argument("--source-tree-sha256", default=SOURCE_TREE_SHA256)
    parser.add_argument("--correction-version", default=CORRECTION_VERSION)
    parser.add_argument("--batch-id", default=BATCH_ID)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        source_ids = load_source_ids(args.source_ids_file)
        result, code = select_batch_rows(
            args.inventory,
            args.split_manifest,
            args.source_acquisition,
            args.ids_output,
            args.report_output,
            source_ids=source_ids,
            expected_source_commit=args.source_commit,
            expected_source_tree_sha256=args.source_tree_sha256,
            correction_version=args.correction_version,
            batch_id=args.batch_id,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        result, code = {"ok": False, "errors": [str(exc)]}, 1

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
