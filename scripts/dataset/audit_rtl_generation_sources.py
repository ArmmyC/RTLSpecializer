"""CLI for metadata-only RTL generation source audits."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_preparation import audit_source_rows, write_audit_reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit local RTL generation source rows without executing data.")
    parser.add_argument("--input", type=Path, default=Path("data/.local_data/verilog-eval-main"))
    parser.add_argument("--output-json", type=Path, default=Path("data/reports/rtl_generation_source_audit_v0.1.json"))
    parser.add_argument("--output-markdown", type=Path, default=Path("data/reports/rtl_generation_source_audit_v0.1.md"))
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args(argv)
    report = audit_source_rows(args.input)
    try:
        write_audit_reports(report, args.output_json, args.output_markdown)
    except OSError as exc:
        report.setdefault("errors", []).append(str(exc))
        code = 1
    else:
        code = 0 if report.get("total_rows", 0) else 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"rows={report.get('total_rows', 0)} readiness={report.get('readiness_categories', {})}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
