#!/usr/bin/env python3
"""Assemble the validated five-task public/private generation inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_smoke_run import assemble_smoke_inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--private-assets", required=True, type=Path)
    parser.add_argument("--tasks-output", required=True, type=Path)
    parser.add_argument("--assets-output", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--correction-report", required=True, type=Path)
    parser.add_argument("--attestation-output", required=True, type=Path)
    args = parser.parse_args(argv)
    result, code = assemble_smoke_inputs(
        normalized_path=args.normalized,
        private_assets_path=args.private_assets,
        tasks_output=args.tasks_output,
        assets_output=args.assets_output,
        correction_manifest_path=args.correction_manifest,
        correction_report_path=args.correction_report,
        attestation_output=args.attestation_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
