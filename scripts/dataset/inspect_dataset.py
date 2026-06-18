#!/usr/bin/env python3
"""Inspect dataset composition without executing dataset content."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.constants import CLAIM_DOMAINS, TOOL_CHECKS
from scripts.dataset.io_utils import load_jsonl


def inspect_dataset(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    loaded, problems = load_jsonl(path)
    if problems:
        return None, [f"{path}{':' + str(p.line) if p.line else ''}: {p.message}" for p in problems]
    counters = {name: Counter() for name in ("split", "source", "task_type", "design_family", "review_status")}
    claim_levels = {domain: Counter() for domain in sorted(CLAIM_DOMAINS)}
    missing_tools = Counter()
    ids = Counter()
    for _, row in loaded:
        ids[row.get("id")] += 1
        for field in ("split", "source", "design_family", "review_status"):
            counters[field][row.get(field)] += 1
        messages = row.get("messages", [])
        task_type = row.get("task_family")
        answer: dict[str, Any] = {}
        if len(messages) == 3 and isinstance(messages[2], dict) and isinstance(messages[2].get("content"), dict):
            answer = messages[2]["content"]
        counters["task_type"][task_type] += 1
        levels = answer.get("claim_levels", {})
        for domain in CLAIM_DOMAINS:
            claim_levels[domain][levels.get(domain, "missing")] += 1
        checks = row.get("tool_checks", {})
        for tool in TOOL_CHECKS:
            if not isinstance(checks, dict) or not checks.get(tool):
                missing_tools[tool] += 1
    return {
        "rows": len(loaded),
        "by_split": dict(sorted(counters["split"].items())),
        "by_source": dict(sorted(counters["source"].items())),
        "by_task_type": dict(sorted(counters["task_type"].items())),
        "by_design_family": dict(sorted(counters["design_family"].items())),
        "by_review_status": dict(sorted(counters["review_status"].items())),
        "claim_levels": {key: dict(sorted(value.items())) for key, value in claim_levels.items()},
        "rows_missing_tool_evidence": dict(sorted(missing_tools.items())),
        "duplicate_ids": sorted(key for key, count in ids.items() if count > 1 and key is not None),
    }, []


def _print_text(result: dict[str, Any]) -> None:
    print("Dataset inspection\n")
    print(f"Rows: {result['rows']}")
    sections = (
        ("By split", "by_split"), ("By source", "by_source"),
        ("By task type", "by_task_type"), ("By design family", "by_design_family"),
        ("By review status", "by_review_status"),
    )
    for title, key in sections:
        print(f"\n{title}:")
        for name, count in result[key].items(): print(f"- {name}: {count}")
    print("\nClaim levels:")
    for domain, counts in result["claim_levels"].items():
        print(f"- {domain}: " + ", ".join(f"{level}={count}" for level, count in counts.items()))
    print("\nRows missing tool evidence:")
    for name, count in result["rows_missing_tool_evidence"].items(): print(f"- {name}: {count}")
    print("\nDuplicate IDs: " + (", ".join(result["duplicate_ids"]) or "none"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result, errors = inspect_dataset(args.input)
    if errors:
        print(json.dumps({"errors": errors}, indent=2) if args.as_json else "\n".join(errors))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False) if args.as_json else "", end="\n" if args.as_json else "")
    if not args.as_json: _print_text(result or {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

