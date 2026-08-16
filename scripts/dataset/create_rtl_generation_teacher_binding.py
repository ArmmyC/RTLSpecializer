"""Create the immutable binding for qualified public teacher packets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_teacher_preparation import (
    create_teacher_generation_binding,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--rtlspecializer-commit", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", help="emit the JSON report (the default output format)")
    args = parser.parse_args(argv)
    report, code = create_teacher_generation_binding(
        args.run_root,
        rtlspecializer_commit=args.rtlspecializer_commit,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
