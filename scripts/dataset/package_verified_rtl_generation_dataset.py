"""Package accepted isolated RTL candidates into generation SFT JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_dataset import package_verified_rtl_generation_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--attempts", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="build and validate in a noncanonical temporary directory without publishing a package",
    )
    parser.add_argument(
        "--preflight-dir",
        type=Path,
        help="noncanonical directory used by --preflight-only",
    )
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument(
        "--asset-qualification",
        type=Path,
        help="passed assetfix qualification report required for the bounded smoke run",
    )
    parser.add_argument(
        "--require-asset-qualification",
        action="store_true",
        help="reject rows unless a passed asset qualification report is supplied",
    )
    parser.add_argument(
        "--qualification-binding",
        type=Path,
        help="passed retry qualification binding required for the assetfix smoke run",
    )
    parser.add_argument("--base-split-manifest", type=Path)
    parser.add_argument("--readiness-split-manifest", type=Path)
    parser.add_argument("--source-acquisition", type=Path)
    parser.add_argument("--asset-correction-report", type=Path)
    parser.add_argument("--qualified-correction-manifest", type=Path)
    parser.add_argument("--expected-rtlbench-commit")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--expected-source-tree-sha256")
    parser.add_argument("--expected-frozen-split-sha256")
    parser.add_argument("--expected-runner-profile")
    parser.add_argument("--expected-runner-image")
    parser.add_argument("--require-smoke-provenance", action="store_true")
    parser.add_argument("--package-id")
    parser.add_argument("--parent-package-id")
    parser.add_argument("--recovery-reason")
    parser.add_argument("--recovery-authorization", type=Path)
    parser.add_argument("--require-recovery-lineage", action="store_true")
    parser.add_argument("--max-variants", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument("--strict", dest="strict", action="store_true")
    strict_group.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="allow missing optional evidence/sidecar metadata",
    )
    parser.set_defaults(strict=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.preflight_only:
        if args.output_dir is not None:
            parser.error("--preflight-only cannot be combined with --output-dir")
        if args.preflight_dir is None:
            parser.error("--preflight-only requires --preflight-dir")
        preflight_dir = args.preflight_dir.resolve()
        canonical_root = (Path.cwd() / "data" / "distill").resolve()
        if preflight_dir == canonical_root or canonical_root in preflight_dir.parents:
            parser.error("preflight directory must not be under data/distill")
        if preflight_dir.exists() and any(preflight_dir.iterdir()):
            parser.error("preflight directory must be absent or empty")
        output_dir = args.preflight_dir
        recovery_lineage = False
    else:
        if args.output_dir is None:
            parser.error("--output-dir is required unless --preflight-only is used")
        output_dir = args.output_dir
        recovery_lineage = args.require_recovery_lineage
    result, code = package_verified_rtl_generation_dataset(
        args.tasks,
        args.candidates,
        args.attempts,
        args.split_manifest,
        output_dir,
        evidence_path=args.evidence,
        sidecar_path=args.sidecar,
        asset_qualification_path=args.asset_qualification,
        qualification_binding_path=args.qualification_binding,
        base_split_path=args.base_split_manifest,
        readiness_split_path=args.readiness_split_manifest,
        source_acquisition_path=args.source_acquisition,
        asset_correction_report_path=args.asset_correction_report,
        qualified_correction_manifest_path=args.qualified_correction_manifest,
        require_asset_qualification=args.require_asset_qualification,
        expected_rtlbench_commit=args.expected_rtlbench_commit,
        expected_source_commit=args.expected_source_commit,
        expected_source_tree_sha256=args.expected_source_tree_sha256,
        expected_frozen_split_sha256=args.expected_frozen_split_sha256,
        expected_runner_profile=args.expected_runner_profile,
        expected_runner_image=args.expected_runner_image,
        require_smoke_provenance=args.require_smoke_provenance,
        package_id=args.package_id,
        parent_package_id=args.parent_package_id,
        recovery_reason=args.recovery_reason,
        recovery_authorization_path=args.recovery_authorization,
        require_recovery_lineage=recovery_lineage,
        max_variants=args.max_variants,
        force=args.force,
        strict=args.strict,
    )
    if args.preflight_only:
        result["preflight_only"] = True
        result["canonical_publication"] = False
        result["consumable"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
