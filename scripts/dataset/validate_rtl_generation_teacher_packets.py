"""Validate the exact public teacher packet set at the manual boundary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_teacher_preparation import (
    validate_teacher_packet_set,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report, code = validate_teacher_packet_set(
        args.run_root,
        binding_path=args.binding,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
