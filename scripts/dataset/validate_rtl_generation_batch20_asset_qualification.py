#!/usr/bin/env python3
"""Validate one isolated batch-20 asset qualification result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.dataset.rtl_generation_asset_qualification import (
    IMAGE_ID,
    RTLBench_COMMIT,
    aggregate_qualification_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification-root", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--qualification-evidence-output", required=True, type=Path)
    parser.add_argument("--expected-image-id", default=IMAGE_ID)
    parser.add_argument("--expected-rtlbench-commit", default=RTLBench_COMMIT)
    parser.add_argument("--handoff-before", type=Path)
    parser.add_argument("--handoff-after", type=Path)
    parser.add_argument("--volumes-before", type=Path)
    parser.add_argument("--volumes-after", type=Path)
    parser.add_argument("--containers-before", type=Path)
    parser.add_argument("--containers-after", type=Path)
    args = parser.parse_args(argv)
    try:
        report = aggregate_qualification_evidence(
            qualification_root=args.qualification_root,
            evidence_path=args.evidence,
            sidecar_path=args.sidecar,
            report_output=args.report_output,
            evidence_output=args.qualification_evidence_output,
            expected_image_id=args.expected_image_id,
            expected_rtlbench_commit=args.expected_rtlbench_commit,
            handoff_before_path=args.handoff_before,
            handoff_after_path=args.handoff_after,
            volumes_before_path=args.volumes_before,
            volumes_after_path=args.volumes_after,
            containers_before_path=args.containers_before,
            containers_after_path=args.containers_after,
        )
    except Exception as exc:  # Keep validation failures machine-readable.
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    result = {"ok": not report["errors"], **report}
    print(json.dumps(result, indent=2, sort_keys=True))
    # A structurally valid run with candidate/asset failures is a valid
    # qualification result.  Only malformed or unsafe evidence is a CLI error.
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
