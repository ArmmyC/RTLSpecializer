#!/usr/bin/env python3
"""Prepare the pinned five-task assetfix_v002 normalization exchange."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_smoke_run import prepare_normalization_smoke_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--base-inventory", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--source-ids-file", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--correction-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--private-output-dir", required=True, type=Path)
    parser.add_argument("--attestation-output", required=True, type=Path)
    args = parser.parse_args(argv)
    result, code = prepare_normalization_smoke_run(
        input_path=args.input,
        source_root=args.source_root,
        inventory_path=args.inventory,
        base_inventory_path=args.base_inventory,
        split_path=args.split_manifest,
        source_ids_path=args.source_ids_file,
        correction_manifest_path=args.correction_manifest,
        correction_report_path=args.correction_report,
        output_dir=args.output_dir,
        private_output_dir=args.private_output_dir,
        attestation_output=args.attestation_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
