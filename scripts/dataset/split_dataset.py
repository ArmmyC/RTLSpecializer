#!/usr/bin/env python3
"""Split a validated dataset, isolating design families by default."""

from __future__ import annotations

import argparse
import json
import random
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.validation import validate_dataset_file


def ratios_valid(train: float, val: float, test: float) -> bool:
    return all(value >= 0 for value in (train, val, test)) and abs(train + val + test - 1.0) <= 1e-9


def split_rows(rows: list[dict[str, Any]], ratios: tuple[float, float, float], seed: int, allow_family_overlap: bool = False) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    names = ("train", "val", "test")
    targets = {name: len(rows) * ratio for name, ratio in zip(names, ratios)}
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    if allow_family_overlap:
        items = rows[:]
        rng.shuffle(items)
        cut_train = round(len(items) * ratios[0])
        cut_val = cut_train + round(len(items) * ratios[1])
        groups = (items[:cut_train], items[cut_train:cut_val], items[cut_val:])
    else:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows: grouped.setdefault(row["design_family"], []).append(row)
        groups_list = list(grouped.values())
        rng.shuffle(groups_list)
        groups_list.sort(key=len, reverse=True)
        assigned: list[list[dict[str, Any]]] = [[], [], []]
        for group in groups_list:
            scores = []
            for index, name in enumerate(names):
                remaining = targets[name] - len(assigned[index])
                scores.append((remaining, ratios[index], -index))
            destination = max(range(3), key=lambda index: scores[index])
            assigned[destination].extend(group)
        groups = tuple(assigned)
    for name, group in zip(names, groups):
        for original in group:
            row = deepcopy(original); row["split"] = name; result[name].append(row)
    return result


def split_dataset(input_path: Path, output_dir: Path, ratios: tuple[float, float, float], seed: int, allow_family_overlap: bool = False, allow_unreviewed: bool = False) -> tuple[dict[str, Any] | None, list[str]]:
    if not ratios_valid(*ratios): return None, ["ratios must be non-negative and sum to 1.0"]
    report = validate_dataset_file(input_path)
    if not report.ok: return None, [item.format() for item in report.errors]
    loaded, problems = load_jsonl(input_path)
    if problems: return None, [problem.message for problem in problems]
    rows = [row for _, row in loaded]
    if not allow_unreviewed:
        bad = [row.get("id") for row in rows if row.get("review_status") not in {"validated", "reviewed"}]
        if bad: return None, [f"non-training-ready rows require --allow-unreviewed: {', '.join(str(x) for x in bad)}"]
    split = split_rows(rows, ratios, seed, allow_family_overlap)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {name: output_dir / f"{name}.jsonl" for name in split}
    for name, output in files.items(): write_jsonl(output, split[name])
    validation_errors: list[str] = []
    for output in files.values():
        output_report = validate_dataset_file(output)
        validation_errors.extend(item.format() for item in output_report.errors)
    if validation_errors: return None, validation_errors
    summary_path = output_dir / "split_summary.json"
    result = {
        "ok": True, "input_rows": len(rows),
        "train_rows": len(split["train"]), "val_rows": len(split["val"]), "test_rows": len(split["test"]),
        "output_files": {**{name: str(path) for name, path in files.items()}, "summary": str(summary_path)},
        "seed": seed, "ratios": {name: ratio for name, ratio in zip(("train", "val", "test"), ratios)},
    }
    summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--train-ratio", type=float, default=.70)
    parser.add_argument("--val-ratio", type=float, default=.15)
    parser.add_argument("--test-ratio", type=float, default=.15)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--allow-family-overlap", action="store_true")
    parser.add_argument("--allow-unreviewed", action="store_true")
    args = parser.parse_args(argv)
    result, errors = split_dataset(args.input, args.output_dir, (args.train_ratio, args.val_ratio, args.test_ratio), args.seed, args.allow_family_overlap, args.allow_unreviewed)
    if errors: print(json.dumps({"ok": False, "errors": errors}, indent=2)); return 1
    print(json.dumps(result, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

