"""CLI for preparing a deterministic RTLBench candidate handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_manual_teacher_verification import prepare_candidate_verification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare RTLBench candidate manifest and workspace without executing RTLBench.")
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--private-assets-root", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--attempt", type=int, choices=range(1, 5))
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--overwrite", "--force", dest="overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = prepare_candidate_verification(
        args.tasks, args.assets, args.private_assets_root, args.candidates,
        args.output_dir, overwrite=args.overwrite, attempt=args.attempt,
        candidate_ids=args.candidate_id,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"prepared_candidates={report.get('prepared_candidates', 0)} ok={report.get('ok', False)}")
        for error in report.get("errors", []):
            print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
