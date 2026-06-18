#!/usr/bin/env python3
"""Evaluate local candidate RTL answers against dataset_v0.1 rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.eval.evaluator import evaluate_dataset, load_candidate_answers, load_dataset_rows


def _print_text(result: dict) -> None:
    if result["ok"]:
        title = "Evaluation completed."
    elif result["matched_rows"]:
        title = "Evaluation completed with warnings."
    else:
        title = "Evaluation failed."
    print(title)
    print()
    print(f"Dataset rows: {result['dataset_rows']}")
    print(f"Candidate rows: {result['candidate_rows']}")
    print(f"Matched rows: {result['matched_rows']}")
    print(f"Missing candidates: {result['missing_candidates']}")
    print(f"Extra candidates: {result['extra_candidates']}")
    print(f"Mean score: {result['mean_score']}")
    print(f"Safety failures: {result['safety_failures']}")
    print(f"Output: {result['output_dir']}")
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
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-name")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    output_dir = args.output_dir / args.run_name if args.run_name else args.output_dir
    dataset_rows, dataset_errors = load_dataset_rows(args.dataset)
    candidate_result = load_candidate_answers(args.candidates)
    if dataset_errors or candidate_result.errors:
        result = {
            "ok": False,
            "dataset_rows": len(dataset_rows),
            "candidate_rows": candidate_result.rows,
            "matched_rows": 0,
            "missing_candidates": 0,
            "extra_candidates": 0,
            "mean_score": 0.0,
            "safety_failures": 0,
            "output_dir": str(output_dir),
            "errors": dataset_errors + candidate_result.errors,
            "warnings": candidate_result.warnings,
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_text(result)
        return 1
    result, code = evaluate_dataset(dataset_rows, candidate_result.candidates, candidate_result.rows, output_dir, args.dataset, args.candidates, args.strict)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
