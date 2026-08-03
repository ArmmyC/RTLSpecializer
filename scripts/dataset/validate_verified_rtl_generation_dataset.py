"""Validate a generated verified-RTL SFT package without executing RTL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_dataset import validate_generation_sft_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--require-asset-qualification", action="store_true")
    parser.add_argument("--asset-qualification", type=Path)
    parser.add_argument("--qualification-binding", type=Path)
    parser.add_argument("--base-split-manifest", type=Path)
    parser.add_argument("--readiness-split-manifest", type=Path)
    parser.add_argument("--source-acquisition", type=Path)
    parser.add_argument("--asset-correction-report", type=Path)
    parser.add_argument("--qualified-correction-manifest", type=Path)
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--expected-source-tree-sha256")
    parser.add_argument("--expected-frozen-split-sha256")
    parser.add_argument("--expected-runner-profile")
    parser.add_argument("--expected-runner-image")
    parser.add_argument("--require-smoke-provenance", action="store_true")
    parser.add_argument("--expected-package-id")
    parser.add_argument("--expected-parent-package-id")
    parser.add_argument("--expected-recovery-reason")
    parser.add_argument("--require-recovery-lineage", action="store_true")
    parser.add_argument("--require-consumable", action="store_true")
    args = parser.parse_args(argv)
    report, code = validate_generation_sft_package(
        args.input_dir,
        require_asset_qualification=args.require_asset_qualification,
        asset_qualification_path=args.asset_qualification,
        qualification_binding_path=args.qualification_binding,
        base_split_path=args.base_split_manifest,
        readiness_split_path=args.readiness_split_manifest,
        source_acquisition_path=args.source_acquisition,
        asset_correction_report_path=args.asset_correction_report,
        qualified_correction_manifest_path=args.qualified_correction_manifest,
        expected_source_commit=args.expected_source_commit,
        expected_source_tree_sha256=args.expected_source_tree_sha256,
        expected_frozen_split_sha256=args.expected_frozen_split_sha256,
        expected_runner_profile=args.expected_runner_profile,
        expected_runner_image=args.expected_runner_image,
        require_smoke_provenance=args.require_smoke_provenance,
        expected_package_id=args.expected_package_id,
        expected_parent_package_id=args.expected_parent_package_id,
        expected_recovery_reason=args.expected_recovery_reason,
        require_recovery_lineage=args.require_recovery_lineage,
        require_consumable=args.require_consumable,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
