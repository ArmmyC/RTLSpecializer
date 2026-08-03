"""Create a hash-bound batch-20 correction manifest without executing RTL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_batch_corrections import create_batch_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result, code = create_batch_manifest(
            args.inventory,
            args.selection,
            args.ids,
            args.correction_root,
            args.output,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        result, code = {"ok": False, "errors": [str(exc)]}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
