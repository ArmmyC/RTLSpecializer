"""CLI for exporting bounded manual RTL teacher repair packets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_manual_teacher_verification import export_teacher_repair_packets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export public manual RTL teacher repair packets.")
    parser.add_argument("--tasks", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--attempts", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--overwrite", "--force", dest="overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = export_teacher_repair_packets(
        args.tasks, args.candidates, args.attempts, args.output_dir,
        max_attempts=args.max_attempts, batch_size=args.batch_size,
        overwrite=args.overwrite,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"repairable_attempts={report.get('repairable_attempts', 0)} packets={report.get('packet_count', 0)} ok={report.get('ok', False)}")
        for error in report.get("errors", []):
            print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
