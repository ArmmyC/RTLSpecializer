#!/usr/bin/env python3
"""Prepare a local VerilogEval-derived draft review batch."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.adapters import ImportOptions, get_adapter
from scripts.dataset.draft_rows import build_draft_row
from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.prepare_review_packet import prepare_review_packet
from scripts.dataset.constants import SOURCES
from scripts.dataset.validation import validate_dataset_file


PREFERRED_FAMILIES = ("fsm", "counter", "shift_register", "mux", "decoder", "register", "arithmetic")
ALLOWED_SOURCES = {"public_verilog_eval"}
GENERATED_FILES = {
    "draft_rows.jsonl", "selected_rows.jsonl", "rejected_rows.jsonl",
    "reviewed_rows.jsonl", "selection_report.json",
}


def _validate_rows(rows: list[dict[str, Any]], output_dir: Path) -> list[str]:
    temp = output_dir / ".validate.tmp.jsonl"
    write_jsonl(temp, rows)
    report = validate_dataset_file(temp, strict=True)
    try:
        temp.unlink()
    except FileNotFoundError:
        pass
    return [item.format() for item in report.errors + report.warnings]


def _selection_details(row: dict[str, Any], preferred_tasks: set[str]) -> tuple[int, list[str]]:
    task = row["messages"][1]["content"]
    summary = task.get("extracted_rtl_summary", {})
    artifacts = task.get("artifacts", {})
    text = json.dumps({"summary": summary, "artifacts": artifacts}, sort_keys=True).lower()
    score = 0
    reasons: list[str] = []
    if row.get("design_family") in PREFERRED_FAMILIES:
        score += 30
        reasons.append(str(row["design_family"]))
    if row.get("task_family") in preferred_tasks:
        score += 20
        reasons.append(f"preferred task: {row['task_family']}")
    if summary.get("clock_signals") or summary.get("reset_signals"):
        score += 15
        reasons.append("clock/reset")
    if summary.get("suspected_counters") or summary.get("suspected_fsm_signals"):
        score += 15
        reasons.append("counter/fsm signals")
    if artifacts.get("testbench"):
        score += 10
        reasons.append("testbench")
    if artifacts.get("lint_log"):
        score += 5
        reasons.append("prompt/specification")
    if len(str(artifacts.get("rtl_code") or "")) > 250:
        score += 5
        reasons.append("substantive RTL")
    if any(word in text for word in ("fsm", "state", "count", "shift", "mux", "decoder", "always_ff")):
        score += 10
        reasons.append("review-relevant RTL pattern")
    return score, reasons


def _selection_key(row: dict[str, Any], preferred_tasks: set[str]) -> tuple[int, str]:
    score, _ = _selection_details(row, preferred_tasks)
    return -score, str(row.get("id"))


def _is_local_data_path(path: Path) -> bool:
    return any(part.lower() == ".local_data" for part in path.resolve().parts)


def _prepare_output_dir(input_path: Path, output_dir: Path, force: bool) -> list[str]:
    try:
        resolved_input = input_path.resolve()
        resolved_output = output_dir.resolve()
    except OSError as exc:
        return [f"could not resolve input/output paths: {exc}"]
    if resolved_output == resolved_input or resolved_output in resolved_input.parents or resolved_input in resolved_output.parents:
        return ["--output-dir must be separate from the input tree (not the input, a parent, or a child)"]
    if _is_local_data_path(resolved_output):
        return ["--output-dir must not be inside .local_data; raw local data must never be replaced"]
    if output_dir.exists() and not output_dir.is_dir():
        return [f"--output-dir exists and is not a directory: {output_dir}"]
    if output_dir.exists() and any(output_dir.iterdir()) and not force:
        return [f"--output-dir is non-empty: {output_dir}; choose an empty directory or rerun with --force"]
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        if force:
            for name in GENERATED_FILES:
                path = output_dir / name
                if path.is_file() or path.is_symlink():
                    path.unlink()
            packet_dir = output_dir / "review_packet"
            if packet_dir.is_symlink():
                packet_dir.unlink()
            elif packet_dir.exists():
                shutil.rmtree(packet_dir)
    except OSError as exc:
        return [f"could not replace generated output under {output_dir}: {exc}"]
    return []


def prepare_batch(
    input_path: Path,
    output_dir: Path,
    limit: int,
    source: str,
    license_text: str | None,
    preferred_tasks: set[str],
    force: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    if not license_text:
        errors.append("--license is required so draft provenance is explicit")
    if limit < 1:
        errors.append("--limit must be at least 1")
    if source not in ALLOWED_SOURCES:
        known = ", ".join(sorted(ALLOWED_SOURCES))
        errors.append(f"invalid --source {source!r}; allowed for this tool: {known} (known dataset sources: {', '.join(sorted(SOURCES))})")
    if not input_path.exists():
        errors.append(f"--input does not exist: {input_path}")
    if errors:
        return _result(False, input_path, source, license_text, 0, 0, 0, 0, output_dir, errors, warnings), 1
    errors.extend(_prepare_output_dir(input_path, output_dir, force))
    if errors:
        return _result(False, input_path, source, license_text, 0, 0, 0, 0, output_dir, errors, warnings), 1
    try:
        adapter = get_adapter("verilog_eval")
    except ValueError as exc:
        return _result(False, input_path, source, license_text, 0, 0, 0, 0, output_dir, [str(exc)], warnings), 1
    discovery = adapter.discover_examples(input_path, ImportOptions(source=source, license=license_text))
    warnings.extend(discovery.warnings)
    warnings.append("reviewed_rows.jsonl is a draft editing template and is not automatically reviewed or promoted")
    rejected = [
        {"source_id": item.source_id, "reason": item.reason, "errors": item.errors, "metadata": item.metadata or {}}
        for item in discovery.rejections
    ]
    draft_rows: list[dict[str, Any]] = []
    source_ids_by_row_id: dict[str, str] = {}
    seen_ids: set[str] = set()
    for example in sorted(discovery.examples, key=lambda item: (item.source_id, json.dumps(item.metadata, sort_keys=True, default=str))):
        row = build_draft_row(example)
        row["review_status"] = "draft"
        row["split"] = "unsplit"
        if row["id"] in seen_ids:
            rejected.append({
                "source_id": example.source_id,
                "row_id": row["id"],
                "reason": "duplicate output row id",
                "errors": [f"duplicate output row id: {row['id']}"],
                "metadata": example.metadata,
            })
            continue
        seen_ids.add(row["id"])
        source_ids_by_row_id[row["id"]] = example.source_id
        draft_rows.append(row)
    valid_rows: list[dict[str, Any]] = []
    for row in draft_rows:
        row_errors = _validate_rows([row], output_dir)
        if row_errors:
            rejected.append({"source_id": row.get("id"), "row_id": row.get("id"), "reason": "generated row failed validation", "errors": row_errors, "metadata": {}})
        else:
            valid_rows.append(row)
    selected = sorted(valid_rows, key=lambda row: _selection_key(row, preferred_tasks))[:limit]
    if not selected:
        errors.append("no rows selected")
    write_jsonl(output_dir / "draft_rows.jsonl", valid_rows)
    write_jsonl(output_dir / "selected_rows.jsonl", selected)
    write_jsonl(output_dir / "rejected_rows.jsonl", rejected)
    write_jsonl(output_dir / "reviewed_rows.jsonl", [deepcopy(row) for row in selected])
    for name in ("draft_rows.jsonl", "selected_rows.jsonl", "reviewed_rows.jsonl"):
        report = validate_dataset_file(output_dir / name, strict=True)
        if not report.ok:
            errors.extend(f"{name}: {item.format()}" for item in report.errors + report.warnings)
    if selected:
        packet_result, packet_code = prepare_review_packet(output_dir / "selected_rows.jsonl", output_dir / "review_packet")
        if packet_code:
            errors.extend(packet_result["errors"])
    report = {
        "input": str(input_path),
        "source": source,
        "license": license_text,
        "discovered_rows": discovery.discovered_examples,
        "valid_draft_rows": len(valid_rows),
        "selected_rows": len(selected),
        "rejected_rows": len(rejected),
        "selection": [dict(
            id=row["id"],
            source_id=source_ids_by_row_id[row["id"]],
            design_family=row["design_family"],
            task_family=row["task_family"],
            selection_score=_selection_details(row, preferred_tasks)[0],
            score_reasons=_selection_details(row, preferred_tasks)[1],
        ) for row in selected],
        "rejected": rejected,
        "warnings": warnings,
        "errors": errors,
    }
    (output_dir / "selection_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    result = _result(not errors, input_path, source, license_text, discovery.discovered_examples, len(valid_rows), len(selected), len(rejected), output_dir, errors, warnings)
    return result, 0 if result["ok"] else 1


def _result(ok: bool, input_path: Path, source: str, license_text: str | None, discovered: int, drafts: int, selected: int, rejected: int, output_dir: Path, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "ok": ok,
        "input": str(input_path),
        "source": source,
        "license": license_text,
        "discovered_rows": discovered,
        "draft_rows": drafts,
        "selected_rows": selected,
        "rejected_rows": rejected,
        "output_dir": str(output_dir),
        "draft_rows_path": str(output_dir / "draft_rows.jsonl"),
        "selected_rows_path": str(output_dir / "selected_rows.jsonl"),
        "rejected_rows_path": str(output_dir / "rejected_rows.jsonl"),
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
    if result["ok"]:
        print("Warning: reviewed_rows.jsonl is a draft editing template, not a reviewed dataset.")
        print("Next: review and edit reviewed_rows.jsonl, validate it, then run promote_reviewed_rows.py")
    else:
        print("Next: fix the errors below; use a new/empty output directory or --force only when replacement is intentional.")
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
    parser.add_argument("--force", action="store_true", help="Replace only this tool's generated files in the exact output directory")
    args = parser.parse_args(argv)
    result, code = prepare_batch(args.input, args.output_dir, args.limit, args.source, args.license, set(args.prefer_task_type), args.force)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
