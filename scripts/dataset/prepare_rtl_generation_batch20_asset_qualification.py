#!/usr/bin/env python3
"""Prepare and authorize one isolated bounded-batch asset qualification task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_asset_qualification import (
    IMAGE_ID,
    RTLBench_COMMIT,
    RTLSPECIALIZER_BASE_COMMIT,
    create_authorization,
    prepare_qualification_input,
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
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--authorization-output", required=True, type=Path)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--rtlbench-root", required=True, type=Path)
    parser.add_argument("--rtlspecializer-root", required=True, type=Path)
    parser.add_argument("--rtlbench-commit", default=RTLBench_COMMIT)
    parser.add_argument("--rtlspecializer-commit", default=RTLSPECIALIZER_BASE_COMMIT)
    parser.add_argument("--image", default=IMAGE_ID)
    parser.add_argument(
        "--expected-correction-manifest-sha256",
        default="2b7cbe7a82f0b73e9960216254565600a9df46bebd66c71fa45f0f401501b8c1",
    )
    args = parser.parse_args(argv)
    try:
        preparation = prepare_qualification_input(
            selection_path=args.selection,
            ids_path=args.ids,
            correction_manifest_path=args.correction_manifest,
            correction_root=args.correction_root,
            inventory_path=args.inventory,
            split_path=args.split,
            authoring_manifest_path=args.authoring_manifest,
            authoring_root=args.authoring_root,
            output_root=args.output_dir,
            expected_correction_manifest_sha256=args.expected_correction_manifest_sha256,
            run_id=args.run_id or args.output_dir.parent.name,
        )
        authorization = create_authorization(
            output_path=args.authorization_output,
            preparation_report=preparation,
            qualification_input=args.output_dir / "input",
            rtlbench_root=args.rtlbench_root,
            rtlspecializer_root=args.rtlspecializer_root,
            rtlbench_commit=args.rtlbench_commit,
            rtlspecializer_commit=args.rtlspecializer_commit,
            image_id=args.image,
        )
    except Exception as exc:  # CLI presents a stable fail-closed JSON error.
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "preparation": preparation, "authorization": authorization}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
