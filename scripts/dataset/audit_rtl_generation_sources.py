"""CLI for metadata-only RTL generation source audits."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_preparation import audit_source_rows, write_audit_reports
from scripts.dataset.rtl_generation_inventory import audit_source_inventory, write_inventory_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit local RTL generation source rows without executing data.")
    parser.add_argument("--input", type=Path, default=Path("data/.local_data/verilog-eval-main"))
    parser.add_argument("--output-json", type=Path, default=Path("data/reports/rtl_generation_source_audit_v0.1.json"))
    parser.add_argument("--output-markdown", type=Path, default=Path("data/reports/rtl_generation_source_audit_v0.1.md"))
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--inventory-output", type=Path)
    parser.add_argument("--acquisition-output", type=Path)
    parser.add_argument("--missing-output", type=Path)
    parser.add_argument("--duplicates-output", type=Path)
    parser.add_argument("--license-output", type=Path)
    parser.add_argument("--correction-manifest", type=Path)
    parser.add_argument("--correction-root", type=Path)
    parser.add_argument("--repository", default="NVlabs/verilog-eval")
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args(argv)
    inventory_mode = any(value is not None for value in (args.source_root, args.source_commit, args.inventory_output, args.acquisition_output))
    if inventory_mode and not all(value is not None for value in (args.source_root, args.source_commit, args.inventory_output, args.acquisition_output)):
        parser.error("--source-root, --source-commit, --inventory-output, and --acquisition-output are required together")
    if inventory_mode:
        inventory_path = args.inventory_output
        assert inventory_path is not None
        stem = inventory_path.name
        if stem.endswith("_rows.jsonl"):
            stem = stem[:-len("_rows.jsonl")]
        elif stem.endswith(".jsonl"):
            stem = stem[:-len(".jsonl")]
        args.missing_output = args.missing_output or inventory_path.with_name(f"{stem}_missing.json")
        args.duplicates_output = args.duplicates_output or inventory_path.with_name(f"{stem}_duplicates.json")
        args.license_output = args.license_output or inventory_path.with_name(f"{stem}_license.json")
        if (args.correction_manifest is None) != (args.correction_root is None):
            parser.error("--correction-manifest and --correction-root must be supplied together")
        report, inventory_rows, acquisition, code = audit_source_inventory(
            args.input,
            args.source_root,
            args.source_commit,
            repository=args.repository,
            correction_manifest=args.correction_manifest,
            correction_root=args.correction_root,
        )
        try:
            write_inventory_outputs(
                report,
                inventory_rows,
                acquisition,
                args.inventory_output,
                args.acquisition_output,
                missing_path=args.missing_output,
                duplicates_path=args.duplicates_output,
                license_path=args.license_output,
            )
            write_audit_reports(report, args.output_json, args.output_markdown)
        except (OSError, ValueError) as exc:
            report.setdefault("errors", []).append(str(exc))
            code = 1
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"rows={report.get('total_rows', 0)} readiness={report.get('readiness_categories', {})}")
        return code

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
