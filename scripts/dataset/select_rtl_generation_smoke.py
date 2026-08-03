"""Select a deterministic five-row executable-ready training smoke batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_inventory import (
    INVENTORY_SCHEMA_VERSION,
    SPLIT_SCHEMA_VERSION,
    _load_inventory,
    validate_generation_split,
)


SMOKE_SCHEMA_VERSION = "rtl_generation_smoke_selection_v0.1"
TARGET_CATEGORIES = (
    "combinational",
    "sequential",
    "counter_or_timer",
    "fsm",
    "reset_sensitive",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_split(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("split manifest must not be a symlink")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != SPLIT_SCHEMA_VERSION:
        raise ValueError("invalid split manifest")
    return value


def _categories(row: dict[str, Any]) -> set[str]:
    values = row.get("behavior_categories")
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        return set(values)
    result = {"sequential" if int(row.get("clock_signal_count", 0) or 0) else "combinational"}
    family = str(row.get("design_family") or "").casefold()
    if "counter" in family or "timer" in family:
        result.add("counter_or_timer")
    if "fsm" in family or "state" in family:
        result.add("fsm")
    if int(row.get("reset_signal_count", 0) or 0):
        result.add("reset_sensitive")
    return result


def select_smoke_rows(
    inventory_path: Path,
    split_path: Path,
    ids_output: Path,
    report_output: Path,
    *,
    count: int = 5,
    base_inventory_path: Path | None = None,
    correction_manifest_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    if count != 5:
        errors.append("the v0.1 smoke selection count must be exactly 5")
    split_inventory_path = base_inventory_path or inventory_path
    split_report, split_code = validate_generation_split(split_inventory_path, split_path, expected_seed=7)
    if split_code:
        errors.extend(split_report.get("errors", []))
    try:
        inventory = _load_inventory(inventory_path)
        split = _load_split(split_path)
        base_inventory = _load_inventory(split_inventory_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1

    if base_inventory_path is not None:
        if correction_manifest_path is None:
            errors.append("correction_manifest_path is required for an overlay inventory")
        if {row["source_id"] for row in inventory} != {row["source_id"] for row in base_inventory}:
            errors.append("overlay inventory source IDs differ from base inventory")
        base_by_id = {row["source_id"]: row for row in base_inventory}
        for row in inventory:
            base = base_by_id.get(row["source_id"])
            if base is None:
                continue
            if row.get("task_id") != base.get("task_id"):
                errors.append(f"overlay task ID changed: {row['source_id']}")
            if row.get("source_commit") != base.get("source_commit"):
                errors.append(f"overlay source commit changed: {row['source_id']}")
        if correction_manifest_path is not None:
            from scripts.dataset.rtl_generation_asset_corrections import load_correction_manifest

            correction_rows, correction_errors = load_correction_manifest(
                correction_manifest_path,
                correction_manifest_path.parent,
            )
            errors.extend(correction_errors)
            correction_ids = {row.get("source_id") for row in correction_rows}
            ready_ids = {
                row["source_id"]
                for row in inventory
                if row.get("verification_readiness") == "executable_ready"
            }
            if ready_ids != correction_ids:
                errors.append("overlay executable-ready IDs differ from correction manifest IDs")

    by_id = {row["source_id"]: row for row in inventory}
    train_ids = split.get("splits", {}).get("train", [])
    eligible = [
        by_id[source_id]
        for source_id in train_ids
        if source_id in by_id and by_id[source_id].get("verification_readiness") == "executable_ready"
    ]
    eligible.sort(key=lambda row: (str(row["source_id"]), str(row.get("task_id", ""))))
    if len(eligible) < count:
        errors.append(f"only {len(eligible)} executable_ready training rows are available; need {count}")

    selected: list[dict[str, Any]] = []
    remaining = list(eligible)
    covered: set[str] = set()
    while remaining and len(selected) < count:
        def score(row: dict[str, Any]) -> tuple[int, int, str, str]:
            new_categories = len(_categories(row) - covered)
            target_new = len((_categories(row) & set(TARGET_CATEGORIES)) - covered)
            return (-target_new, -new_categories, str(row["source_id"]), str(row.get("task_id", "")))

        choice = min(remaining, key=score)
        remaining.remove(choice)
        selected.append(choice)
        covered.update(_categories(choice))

    selected_rows = [
        {
            "source_id": row["source_id"],
            "task_id": row["task_id"],
            "design_family": row.get("design_family"),
            "verification_readiness": row.get("verification_readiness"),
            "behavior_categories": sorted(_categories(row)),
        }
        for row in selected
    ]
    missing_categories = sorted(set(TARGET_CATEGORIES) - covered)
    report = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "inventory_schema_version": INVENTORY_SCHEMA_VERSION,
        "inventory_sha256": _sha256_file(inventory_path),
        "base_inventory_sha256": _sha256_file(base_inventory_path) if base_inventory_path is not None else None,
        "correction_manifest_sha256": _sha256_file(correction_manifest_path) if correction_manifest_path is not None else None,
        "split_sha256": _sha256_file(split_path),
        "split": "train",
        "requested_count": count,
        "eligible_training_count": len(eligible),
        "selected_count": len(selected_rows),
        "selection_algorithm": "coverage_greedy_source_id_tiebreak_v1",
        "requested_categories": list(TARGET_CATEGORIES),
        "covered_categories": sorted(covered & set(TARGET_CATEGORIES)),
        "missing_categories": missing_categories,
        "rows": selected_rows,
        "errors": sorted(set(errors)),
    }
    if errors or len(selected_rows) != count or ids_output.exists() or ids_output.is_symlink() or report_output.exists() or report_output.is_symlink():
        if ids_output.exists() or report_output.exists():
            report["errors"].append("smoke output already exists")
        return report, 1

    ids_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    ids_output.write_text("".join(f"{row['source_id']}\n" for row in selected_rows), encoding="utf-8")
    report_output.write_text(json.dumps({**report, "ok": True}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**report, "ok": True, "ids_output": ids_output.as_posix(), "report_output": report_output.as_posix()}, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--ids-output", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--base-inventory", type=Path)
    parser.add_argument("--correction-manifest", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = select_smoke_rows(
        args.inventory,
        args.split_manifest,
        args.ids_output,
        args.report_output,
        base_inventory_path=args.base_inventory,
        correction_manifest_path=args.correction_manifest,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
