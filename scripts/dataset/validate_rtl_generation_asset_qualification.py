#!/usr/bin/env python3
"""Validate isolated RTLBench mutation qualification for assetfix_v002."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_smoke_run import validate_asset_qualification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--correction-report", required=True, type=Path)
    parser.add_argument("--qualification-manifest", required=True, type=Path)
    parser.add_argument("--qualification-evidence", required=True, type=Path)
    parser.add_argument("--qualification-sidecar", required=True, type=Path)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--expected-image-id")
    parser.add_argument("--expected-rtlbench-commit", default="fcad47eb03e469097432229e1285b9239fd23a00")
    args = parser.parse_args(argv)
    result, code = validate_asset_qualification(
        tasks_path=args.tasks,
        assets_path=args.assets,
        correction_manifest_path=args.correction_manifest,
        correction_report_path=args.correction_report,
        qualification_manifest_path=args.qualification_manifest,
        qualification_evidence_path=args.qualification_evidence,
        qualification_sidecar_path=args.qualification_sidecar,
        workspace_root=args.workspace_root,
        report_output=args.report_output,
        expected_image_id=args.expected_image_id,
        expected_rtlbench_commit=args.expected_rtlbench_commit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
