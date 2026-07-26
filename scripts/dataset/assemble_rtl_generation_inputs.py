"""CLI for deterministic task/private-asset assembly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_preparation import assemble_generation_inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Join normalized RTL generation tasks with private verification assets.")
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--private-assets", required=True, type=Path)
    parser.add_argument("--tasks-output", required=True, type=Path)
    parser.add_argument("--assets-output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = assemble_generation_inputs(
        args.normalized, args.private_assets, args.tasks_output, args.assets_output, force=args.force,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"rows={result.get('rows', 0)} ok={result.get('ok', False)}")
        for error in result.get("errors", []): print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
