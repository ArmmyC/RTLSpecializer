#!/usr/bin/env python3
"""Prepare a local VerilogEval-derived draft review batch."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.adapters import ImportOptions, get_adapter
from scripts.dataset.draft_rows import build_draft_row
from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.prepare_review_packet import prepare_review_packet
from scripts.dataset.validation import validate_dataset_file


PREFERRED_FAMILIES = ("fsm", "counter", "shift_register", "mux", "decoder", "register", "arithmetic")


def _validate_rows(rows: list[dict[str, Any]], output_dir: Path) -> list[str]:
    temp = output_dir / ".validate.tmp.jsonl"
    write_jsonl(temp, rows)
    report = validate_dataset_file(temp, strict=True)
    try:
        temp.unlink()
    except FileNotFoundError:
        pass
    return [item.format() for item in report.errors + report.warnings]


def _selection_score(row: dict[str, Any], preferred_tasks: set[str]) -> tuple[int, str]:
    task = row["messages"][1]["content"]
    summary = task.get("extracted_rtl_summary", {})
    artifacts = task.get("artifacts", {})
    text = json.dumps({"summary": summary, "artifacts": artifacts}, sort_keys=True).lower()
    score = 0
    if row.get("design_family") in PREFERRED_FAMILIES:
        score += 30
    if row.get("task_family") in preferred_tasks:
        score += 20
    if summary.get("clock_signals") or summary.get("reset_signals"):
        score += 15
    if summary.get("suspected_counters") or summary.get("suspected_fsm_signals"):
        score += 15
    if artifacts.get("testbench"):
        score += 10
    if artifacts.get("lint_log"):
        score += 5
    if len(str(artifacts.get("rtl_code") or "")) > 250:
        score += 5
    if any(word in text for word in ("fsm", "state", "count", "shift", "mux", "decoder", "always_ff")):
        score += 10
    return (-score, str(row.get("id")))


def prepare_batch(
    input_path: Path,
    output_dir: Path,
    limit: int,
    source: str,
    license_text: str | None,
    preferred_tasks: set[str],
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    if not license_text:
        errors.append("--license is required so draft provenance is explicit")
    if limit < 1:
        errors.append("--limit must be at least 1")
    if errors:
        return _result(False, 0, 0, 0, 0, output_dir, errors, warnings), 1
    try:
        adapter = get_adapter("verilog_eval")
    except ValueError as exc:
        return _result(False, 0, 0, 0, 0, output_dir, [str(exc)], warnings), 1
    discovery = adapter.discover_examples(input_path, ImportOptions(source=source, license=license_text))
    warnings.extend(discovery.warnings)
    rejected = [
        {"source_id": item.source_id, "reason": item.reason, "errors": item.errors, "metadata": item.metadata or {}}
        for item in discovery.rejections
    ]
    draft_rows: list[dict[str, Any]] = []
    for example in discovery.examples:
        row = build_draft_row(example)
        row["review_status"] = "draft"
        row["split"] = "unsplit"
        draft_rows.append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    valid_rows: list[dict[str, Any]] = []
    for row in draft_rows:
        row_errors = _validate_rows([row], output_dir)
        if row_errors:
            rejected.append({"source_id": row.get("id"), "reason": "generated row failed validation", "errors": row_errors, "metadata": {}})
        else:
            valid_rows.append(row)
    selected = sorted(valid_rows, key=lambda row: _selection_score(row, preferred_tasks))[:limit]
    if not selected:
        errors.append("no rows selected")
    write_jsonl(output_dir / "draft_rows.jsonl", valid_rows)
    write_jsonl(output_dir / "selected_rows.jsonl", selected)
    write_jsonl(output_dir / "rejected_rows.jsonl", rejected)
    write_jsonl(output_dir / "reviewed_rows.jsonl", [deepcopy(row) for row in selected])
    if selected:
        packet_result, packet_code = prepare_review_packet(output_dir / "selected_rows.jsonl", output_dir / "review_packet")
        if packet_code:
            errors.extend(packet_result["errors"])
    report = {
        "input": str(input_path),
        "selection": [{"id": row["id"], "design_family": row["design_family"], "task_family": row["task_family"]} for row in selected],
        "rejected": rejected,
        "warnings": warnings,
    }
    (output_dir / "selection_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    result = _result(not errors, discovery.discovered_examples, len(valid_rows), len(selected), len(rejected), output_dir, errors, warnings)
    return result, 0 if result["ok"] else 1


def _result(ok: bool, discovered: int, drafts: int, selected: int, rejected: int, output_dir: Path, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ok": ok,
        "discovered_rows": discovered,
        "draft_rows": drafts,
        "selected_rows": selected,
        "rejected_rows": rejected,
        "output_dir": str(output_dir),
        "draft_rows_path": str(output_dir / "draft_rows.jsonl"),
        "selected_rows_path": str(output_dir / "selected_rows.jsonl"),
        "review_packet_dir": str(output_dir / "review_packet"),
        "reviewed_rows_template": str(output_dir / "reviewed_rows.jsonl"),
        "selection_report": str(output_dir / "selection_report.json"),
        "errors": errors,
        "warnings": warnings,
    }


def _print_text(result: dict[str, Any]) -> None:
    print("VerilogEval review batch prepared." if result["ok"] else "VerilogEval review batch failed.")
    print()
    print(f"Discovered rows: {result['discovered_rows']}")
    print(f"Draft rows: {result['draft_rows']}")
    print(f"Selected rows: {result['selected_rows']}")
    print(f"Rejected rows: {result['rejected_rows']}")
    print(f"Output: {result['output_dir']}")
    print("Next: edit reviewed_rows.jsonl, then run promote_reviewed_rows.py")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--source", default="public_verilog_eval")
    parser.add_argument("--license")
    parser.add_argument("--prefer-task-type", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = prepare_batch(args.input, args.output_dir, args.limit, args.source, args.license, set(args.prefer_task_type))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
