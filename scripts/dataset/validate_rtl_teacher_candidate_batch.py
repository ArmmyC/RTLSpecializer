"""CLI for strict validation of a manually returned RTL teacher response."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_manual_teacher_verification import validate_teacher_candidate_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a manual RTL teacher candidate response.")
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--private-assets", type=Path)
    parser.add_argument("--private-assets-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", "--force", dest="overwrite", action="store_true")
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report, code = validate_teacher_candidate_batch(
        args.packet, args.response, private_assets_path=args.private_assets,
        private_assets_root=args.private_assets_root, output_path=args.output,
        overwrite=args.overwrite, append=args.append,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"validated_candidates={report.get('validated_candidates', 0)} ok={report.get('ok', False)}")
        for error in report.get("errors", []):
            print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
