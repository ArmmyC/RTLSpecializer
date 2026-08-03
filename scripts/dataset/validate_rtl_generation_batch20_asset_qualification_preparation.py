#!/usr/bin/env python3
"""Validate a batch-20 qualification task before isolated execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.dataset.rtl_generation_asset_qualification import (
    IMAGE_ID,
    RTLBench_COMMIT,
    RTLSPECIALIZER_BASE_COMMIT,
    validate_prepared_qualification,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--authoring-manifest", required=True, type=Path)
    parser.add_argument("--authoring-root", required=True, type=Path)
    parser.add_argument("--qualification-root", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    parser.add_argument("--correction-static-report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rtlbench-root", type=Path)
    parser.add_argument("--rtlspecializer-root", type=Path)
    parser.add_argument("--rtlbench-commit", default=RTLBench_COMMIT)
    parser.add_argument("--rtlspecializer-commit", default=RTLSPECIALIZER_BASE_COMMIT)
    parser.add_argument("--image", default=IMAGE_ID)
    args = parser.parse_args(argv)
    try:
        report = validate_prepared_qualification(
            selection_path=args.selection,
            ids_path=args.ids,
            correction_manifest_path=args.correction_manifest,
            correction_root=args.correction_root,
            inventory_path=args.inventory,
            split_path=args.split,
            authoring_manifest_path=args.authoring_manifest,
            authoring_root=args.authoring_root,
            qualification_root=args.qualification_root,
            authorization_path=args.authorization,
            report_output=args.output,
            correction_static_report_path=args.correction_static_report,
            rtlbench_root=args.rtlbench_root,
            rtlspecializer_root=args.rtlspecializer_root,
            expected_image_id=args.image,
            expected_rtlbench_commit=args.rtlbench_commit,
            expected_rtlspecializer_commit=args.rtlspecializer_commit,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "report": report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
