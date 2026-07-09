#!/usr/bin/env python3
"""Generate evaluator-ready candidates with an OpenAI-compatible chat endpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.eval.openai_compatible_candidate_runner import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_RETRIES,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    OpenAICompatibleRunnerConfig,
    run_openai_compatible_candidates,
)


def _print_text(summary: dict[str, object]) -> None:
    title = (
        "OpenAI-compatible candidate generation completed."
        if summary["ok"]
        else "OpenAI-compatible candidate generation failed."
    )
    print(title)
    print(f"Dataset: {summary['dataset']}")
    print(f"Output: {summary['output']}")
    print(f"Selected rows: {summary['selected_rows']}")
    print(f"Skipped rows: {summary['skipped_rows']}")
    print(f"Written rows: {summary['written_rows']}")
    print(f"Candidate rows: {summary['candidate_rows']}")
    print(f"Parse error rows: {summary['parse_error_rows']}")
    print(f"API error rows: {summary['api_error_rows']}")
    if summary["warnings"]:
        print("Warnings:")
        for item in summary["warnings"]:
            print(f"- {item}")
    if summary["errors"]:
        print("Errors:")
        for item in summary["errors"]:
            print(f"- {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--raw-output-dir", type=Path)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--schema-reminder")
    parser.add_argument("--schema-reminder-file", type=Path)
    parser.add_argument("--response-format-json", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    summary, code = run_openai_compatible_candidates(OpenAICompatibleRunnerConfig(
        dataset=args.dataset,
        output=args.output,
        base_url=args.base_url,
        model=args.model,
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        limit=args.limit,
        resume=args.resume,
        timeout=args.timeout,
        raw_output_dir=args.raw_output_dir,
        retries=args.retries,
        fail_fast=args.fail_fast,
        schema_reminder=args.schema_reminder,
        schema_reminder_file=args.schema_reminder_file,
        response_format_json=args.response_format_json,
    ))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_text(summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
