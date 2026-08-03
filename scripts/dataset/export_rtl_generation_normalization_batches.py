"""CLI for public/private RTL generation task preparation export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_preparation import export_generation_normalization_batches
from scripts.dataset.rtl_generation_inventory import load_split_source_ids, validate_generation_split


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export teacher-visible RTL generation normalization batches.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--private-output-dir", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--source-root", type=Path, help="local checkout root used for operator provenance")
    parser.add_argument("--source-commit", help="pinned 40-character upstream commit")
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--split", choices=("train", "validation", "test"))
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--source-ids-file", type=Path)
    parser.add_argument("--correction-manifest", type=Path)
    parser.add_argument("--correction-root", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if (args.split_manifest is None) != (args.split is None):
        parser.error("--split-manifest and --split must be supplied together")
    if (args.correction_manifest is None) != (args.correction_root is None):
        parser.error("--correction-manifest and --correction-root must be supplied together")
    source_ids = None
    selection_errors = []
    if args.source_root is not None:
        if args.source_root.is_symlink() or not args.source_root.is_dir():
            selection_errors.append("--source-root must be an existing non-symlink directory")
        else:
            try:
                args.input.resolve().relative_to(args.source_root.resolve())
            except (OSError, ValueError):
                selection_errors.append("--input must be beneath --source-root")
    if args.split_manifest is not None:
        if args.inventory is None:
            selection_errors.append("--inventory is required with --split-manifest")
            split_code = 1
        elif args.source_commit is None:
            selection_errors.append("--source-commit is required with --split-manifest")
            split_code = 1
        else:
            _, split_code = validate_generation_split(args.inventory, args.split_manifest)
        if split_code != 0:
            selection_errors.append("split manifest failed validation")
        else:
            source_ids, selection_errors = load_split_source_ids(
                args.split_manifest,
                split=args.split,
                inventory_path=args.inventory,
                source_ids_path=args.source_ids_file,
            )
    elif args.source_ids_file is not None:
        try:
            source_ids = [line.strip() for line in args.source_ids_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeError) as exc:
            selection_errors.append(f"could not read source-ID allowlist: {exc}")
    if selection_errors:
        result = {"ok": False, "exported_rows": 0, "errors": sorted(set(selection_errors))}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for error in result["errors"]:
                print(f"error: {error}")
        return 1
    result, code = export_generation_normalization_batches(
        args.input, args.output_dir, args.private_output_dir,
        batch_size=args.batch_size, limit=args.limit, start_index=args.start_index, force=args.force,
        source_commit=args.source_commit, source_ids=source_ids,
        correction_manifest=args.correction_manifest,
        correction_root=args.correction_root,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"exported_rows={result.get('exported_rows', 0)} ok={result.get('ok', False)}")
        for error in result.get("errors", []): print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
