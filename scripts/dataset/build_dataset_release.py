#!/usr/bin/env python3
"""Build a deterministic dataset release directory from local dataset_v0.1 JSONL inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.release import ReleaseConfig, build_release


def _print_text(result: dict) -> None:
    print("Dataset release built." if result["ok"] else "Dataset release failed.")
    print()
    print(f"Release: {result['release_name']}")
    print(f"Input files: {result['input_files']}")
    print(f"Input rows: {result['input_rows']}")
    print(f"Accepted rows: {result['accepted_rows']}")
    print(f"Rejected rows: {result['rejected_rows']}")
    print(f"Train rows: {result['train_rows']}")
    print(f"Val rows: {result['val_rows']}")
    print(f"Test rows: {result['test_rows']}")
    print(f"Output: {result['output_dir']}")
    if result["errors"]:
        print()
        print("Errors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print()
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-name", required=True)
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--train-ratio", type=float, default=.70)
    parser.add_argument("--val-ratio", type=float, default=.15)
    parser.add_argument("--test-ratio", type=float, default=.15)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--allow-family-overlap", action="store_true")
    parser.add_argument("--allow-source-overlap", action="store_true")
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = build_release(ReleaseConfig(
        release_name=args.release_name,
        output_root=args.output_dir,
        input_paths=args.input,
        ratios=(args.train_ratio, args.val_ratio, args.test_ratio),
        seed=args.seed,
        allow_family_overlap=args.allow_family_overlap,
        allow_source_overlap=args.allow_source_overlap,
        min_rows=args.min_rows,
        strict=args.strict,
    ))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
