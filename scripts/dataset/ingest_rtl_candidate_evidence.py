"""CLI for strict local ingestion of RTLBench candidate evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_manual_teacher_verification import ingest_candidate_evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest rtl_candidate_evidence_v0.1 without executing RTLBench.")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = ingest_candidate_evidence(args.plan, args.evidence, args.output)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ingested_attempts={report.get('ingested_attempts', 0)} ok={report.get('ok', False)}")
        for error in report.get("errors", []):
            print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
