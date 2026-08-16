"""Validate a human-supplied review record for the immutable smoke package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_human_review import validate_human_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result, code = validate_human_review(args.package, args.review)
        if args.report_output is not None:
            if args.report_output.exists() or args.report_output.is_symlink():
                raise ValueError(f"refusing to replace existing output: {args.report_output.name}")
            args.report_output.parent.mkdir(parents=True, exist_ok=True)
            with args.report_output.open("xb") as handle:
                handle.write((json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            args.report_output.chmod(0o600)
    except (OSError, UnicodeError, ValueError) as exc:
        result, code = {"ok": False, "errors": [str(exc)]}, 1

    result = {**result, "ok": code == 0}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
