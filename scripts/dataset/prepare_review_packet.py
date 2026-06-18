#!/usr/bin/env python3
"""Prepare Markdown/JSON review packets for imported public draft rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.review_promotion import artifact_items, load_rows
from scripts.dataset.validation import validate_dataset_file


CHECKLIST = [
    "Issue is visible in supplied artifact.",
    "Evidence names concrete signals or report fields.",
    "Time reasoning addresses clock/reset/latency/state risk.",
    "Space reasoning addresses area/activity resources.",
    "Claim levels match available evidence.",
    "Verification plan includes lint/compile and relevant checks.",
    "No power claim without power report.",
    "No private/proprietary data included.",
]


def _safe_name(row_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in row_id)


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    task = row.get("messages", [{}, {}, {}])[1].get("content", {})
    return {
        "id": row.get("id"),
        "source": row.get("source"),
        "license": row.get("license"),
        "design_family": row.get("design_family"),
        "task_type": task.get("task_type") if isinstance(task, dict) else row.get("task_family"),
        "user_goal": task.get("user_goal") if isinstance(task, dict) else None,
    }


def _review_markdown(row: dict[str, Any]) -> str:
    meta = _metadata(row)
    provenance = row.get("provenance", {})
    answer = row.get("messages", [{}, {}, {}])[2].get("content", {})
    lines = [
        f"# Review packet: {meta['id']}",
        "",
        "> Warning: imported answers are draft stubs until a reviewer rewrites and validates them.",
        "",
        "## Metadata",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for key, value in meta.items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(["", "## Provenance", "", "```json", json.dumps(provenance, indent=2), "```", "", "## Supplied artifacts", ""])
    for name, value in artifact_items(row):
        lines.extend([f"### `{name}`", "", "```systemverilog", value, "```", ""])
    lines.extend([
        "## Current assistant answer",
        "",
        "```json",
        json.dumps(answer, indent=2),
        "```",
        "",
        "## Review checklist",
        "",
    ])
    lines.extend(f"- [ ] {item}" for item in CHECKLIST)
    lines.extend([
        "",
        "## Recommended next action",
        "",
        "Rewrite the assistant answer so every issue is grounded in supplied artifacts. Keep claim levels conservative unless matching tool evidence is present.",
        "",
    ])
    return "\n".join(lines)


def prepare_review_packet(input_path: Path, output_dir: Path) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    report = validate_dataset_file(input_path, strict=True)
    if not report.ok:
        errors.extend(item.format() for item in report.errors + report.warnings)
    rows, load_errors = load_rows(input_path)
    errors.extend(load_errors)
    if errors:
        return {
            "ok": False, "input_rows": len(rows), "packet_rows": 0,
            "output_dir": str(output_dir), "manifest": str(output_dir / "review_manifest.jsonl"),
            "errors": errors, "warnings": warnings,
        }, 1
    rows_dir = output_dir / "rows"
    rows_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row["id"])
        stem = _safe_name(row_id)
        json_path = rows_dir / f"{stem}.json"
        md_path = rows_dir / f"{stem}.review.md"
        json_path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(_review_markdown(row), encoding="utf-8")
        manifest_rows.append({
            "id": row_id,
            "source": row.get("source"),
            "license": row.get("license"),
            "task_type": row.get("task_family"),
            "design_family": row.get("design_family"),
            "review_markdown": str(Path("rows") / md_path.name),
            "row_json": str(Path("rows") / json_path.name),
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "review_manifest.jsonl", manifest_rows)
    (output_dir / "README.md").write_text(
        "# Public draft review packet\n\n"
        "This packet contains local copies of imported public draft rows. "
        "Do not treat imported answers as training labels until they are edited, validated, and promoted.\n\n"
        "Review each `rows/*.review.md`, edit rows outside this packet into a reviewed JSONL file, then run "
        "`scripts/dataset/promote_reviewed_rows.py`.\n",
        encoding="utf-8",
    )
    result = {
        "ok": True,
        "input_rows": len(rows),
        "packet_rows": len(manifest_rows),
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "review_manifest.jsonl"),
        "errors": [],
        "warnings": warnings,
    }
    return result, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = prepare_review_packet(args.input, args.output_dir)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["ok"]:
        print("Review packet created.")
        print(f"Input rows: {result['input_rows']}")
        print(f"Packet rows: {result['packet_rows']}")
        print(f"Output dir: {result['output_dir']}")
        print(f"Manifest: {result['manifest']}")
    else:
        print("Review packet generation failed.")
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
