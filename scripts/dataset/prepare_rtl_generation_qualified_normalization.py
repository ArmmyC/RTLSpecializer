"""Prepare a public normalization batch from passed qualification rows.

This is a control-plane CLI.  It initializes a standard manual-generation run,
binds the immutable qualification outputs, and exports public packets plus
private verification assets.  It never calls a model or executes RTL.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace_layout import initialize_manual_rtl_run
from scripts.dataset.rtl_generation_qualified_subset import (
    prepare_qualified_normalization_run,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare a qualified train-only RTL normalization batch."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--selection-ids", required=True, type=Path)
    parser.add_argument("--qualification-report", required=True, type=Path)
    parser.add_argument("--qualification-output-hashes", required=True, type=Path)
    parser.add_argument("--qualified-task-ids", required=True, type=Path)
    parser.add_argument("--failed-task-ids", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        initialized = initialize_manual_rtl_run(
            args.run_id,
            "VerilogEval",
            args.runs_root,
            resume=args.resume,
        )
        run_root = args.runs_root / args.run_id
        result, code = prepare_qualified_normalization_run(
            source_input=args.input,
            source_root=args.source_root,
            inventory_path=args.inventory,
            split_path=args.split,
            selection_ids_path=args.selection_ids,
            qualification_report_path=args.qualification_report,
            qualification_output_hashes_path=args.qualification_output_hashes,
            qualified_task_ids_path=args.qualified_task_ids,
            failed_task_ids_path=args.failed_task_ids,
            correction_manifest_path=args.correction_manifest,
            correction_root=args.correction_root,
            run_root=run_root,
        )
        result = {"initialized": initialized, **result}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result = {"ok": False, "stage": "initialization", "errors": [str(exc)]}
        code = 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"ok={result.get('ok', False)}")
        for error in result.get("errors", []):
            print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
