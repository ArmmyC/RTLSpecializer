#!/usr/bin/env python3
"""Generate evaluator-ready candidates with an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.eval.model_candidate_runner import DEFAULT_ENDPOINT, RunnerConfig, run_model_candidates
from scripts.eval.model_prompting import DEFAULT_PROMPT_TEMPLATE


def _print_text(report: dict) -> None:
    print("Model candidate generation completed." if report["ok"] else "Model candidate generation failed.")
    print(f"Dataset: {report['dataset']}")
    print(f"Output: {report['output']}")
    print(f"Attempted rows: {report['attempted_rows']}")
    print(f"Written rows: {report['written_rows']}")
    print(f"Skipped rows: {report['skipped_rows']}")
    if report["errors"]:
        print("Errors:")
        for item in report["errors"]:
            print(f"- {item}")
    if report["warnings"]:
        print("Warnings:")
        for item in report["warnings"]:
            print(f"- {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--api-key-env")
    parser.add_argument("--prompt-template", default=DEFAULT_PROMPT_TEMPLATE)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--row-id", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--raw-output-dir", type=Path)
    parser.add_argument("--evaluate-output-dir", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-nonlocal-endpoint", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = run_model_candidates(RunnerConfig(
        dataset=args.dataset,
        output=args.output,
        model=args.model,
        endpoint=args.endpoint,
        api_key_env=args.api_key_env,
        prompt_template=args.prompt_template,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        retries=args.retries,
        limit=args.limit,
        row_ids=tuple(args.row_id),
        resume=args.resume,
        overwrite=args.overwrite,
        raw_output_dir=args.raw_output_dir,
        evaluate_output_dir=args.evaluate_output_dir,
        strict=args.strict,
        dry_run=args.dry_run,
        allow_nonlocal_endpoint=args.allow_nonlocal_endpoint,
    ))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
