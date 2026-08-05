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
    load_selection_metadata,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
    load_source_ids,
    select_batch_rows,
    sha256_file,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--source-acquisition", required=True, type=Path)
    parser.add_argument("--ids-output", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--source-ids-file", type=Path)
    parser.add_argument("--selection-metadata", type=Path)
    parser.add_argument("--excluded-source-ids-file", type=Path)
    parser.add_argument("--expected-count", type=int, default=20)
    parser.add_argument("--source-commit", default=SOURCE_COMMIT)
    parser.add_argument("--source-tree-sha256", default=SOURCE_TREE_SHA256)
    parser.add_argument("--correction-version", default=CORRECTION_VERSION)
    parser.add_argument("--batch-id", default=BATCH_ID)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        source_ids = load_source_ids(args.source_ids_file)
        selection_roles, diversity_tags, required_targets, advisory_targets = load_selection_metadata(args.selection_metadata)
        excluded_source_ids = load_source_ids(args.excluded_source_ids_file) if args.excluded_source_ids_file else None
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
            expected_count=args.expected_count,
            selection_roles=selection_roles or None,
            diversity_tags=diversity_tags or None,
            required_targets=required_targets if args.selection_metadata else None,
            advisory_targets=advisory_targets if args.selection_metadata else None,
            excluded_source_ids=excluded_source_ids,
            enforce_bounded_size=True,
            selection_metadata_sha256=(
                sha256_file(args.selection_metadata)
                if args.selection_metadata is not None
                else None
            ),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        result, code = {"ok": False, "errors": [str(exc)]}, 1

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
