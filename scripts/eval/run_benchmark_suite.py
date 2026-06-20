#!/usr/bin/env python3
"""Run a local multi-model benchmark suite from a JSON config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.eval.benchmark_suite import SuiteOptions, run_benchmark_suite


def _print_text(summary: dict) -> None:
    print("Benchmark suite completed." if summary["ok"] else "Benchmark suite failed.")
    print(f"Run ID: {summary['run_id']}")
    print(f"Dataset: {summary['dataset']}")
    print(f"Results: {len(summary['results'])}")
    print(f"Output: {summary['output_dir']}")
    if summary["errors"]:
        print("Errors:")
        for error in summary["errors"]:
            print(f"- {error}")
    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--row-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--skip-candidates", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--allow-nonlocal-endpoint", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary, code = run_benchmark_suite(SuiteOptions(
        config=args.config,
        output_dir=args.output_dir,
        run_id=args.run_id,
        limit=args.limit,
        row_ids=tuple(args.row_id),
        dry_run=args.dry_run,
        resume=args.resume,
        overwrite=args.overwrite,
        skip_candidates=args.skip_candidates,
        evaluate_only=args.evaluate_only,
        allow_nonlocal_endpoint=args.allow_nonlocal_endpoint,
    ))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_text(summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
