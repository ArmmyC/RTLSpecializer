"""Validate a complete public teacher-packet response set atomically."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_manual_teacher_verification import (
    validate_teacher_candidate_response_set,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", required=True, type=Path)
    parser.add_argument("--responses", required=True, type=Path)
    parser.add_argument("--private-assets", required=True, type=Path)
    parser.add_argument("--private-assets-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = validate_teacher_candidate_response_set(
        args.packets,
        args.responses,
        private_assets_path=args.private_assets,
        private_assets_root=args.private_assets_root,
        output_path=args.output,
        overwrite=args.overwrite,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
