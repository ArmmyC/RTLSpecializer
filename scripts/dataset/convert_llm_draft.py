#!/usr/bin/env python3
"""Validate offline LLM drafts and separate accepted and rejected rows."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.validation import validate_dataset_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    loaded, problems = load_jsonl(args.input)
    if problems:
        print(json.dumps({"ok": False, "errors": [p.message for p in problems]}, indent=2)); return 1
    accepted, rejected = [], []
    for line, row in loaded:
        candidate = dict(row)
        candidate.setdefault("review_status", "draft")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "row.jsonl"; write_jsonl(path, [candidate])
            report = validate_dataset_file(path)
        if report.ok: accepted.append(candidate)
        else: rejected.append({"line": line, "row": candidate, "errors": [e.message for e in report.errors]})
    if accepted: write_jsonl(args.output, accepted)
    rejected_path = args.output.with_suffix(".rejected.jsonl"); write_jsonl(rejected_path, rejected)
    result = {"ok": bool(accepted), "input_rows": len(loaded), "accepted_rows": len(accepted), "rejected_rows": len(rejected), "output": str(args.output), "rejected_output": str(rejected_path)}
    print(json.dumps(result, indent=2)); return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())

