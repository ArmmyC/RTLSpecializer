"""Validate and assemble immutable train-only RTL generation packages.

This module is deliberately metadata-first.  It consumes already verified
generation packages and a frozen source split; it never reads source RTL or
testbenches, calls a model, executes generated code, or runs an EDA tool.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.rtl_generation_dataset import (
    GENERATION_SFT_SCHEMA_VERSION,
    PACKAGE_SCHEMA_VERSION,
    _contains_private_marker,
    validate_generation_sft_row,
)
from scripts.dataset.rtl_generation_human_review import package_tree_sha256


COVERAGE_SCHEMA_VERSION = "rtl_generation_train_coverage_v0.1"
UNION_MANIFEST_SCHEMA_VERSION = "rtl_generation_train_union_package_v0.1"
EXPECTED_SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
EXPECTED_SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
EXPECTED_FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
EXPECTED_TRAIN_COUNT = 111
PACKAGE_OUTPUT_NAMES = (
    "all.jsonl",
    "train.jsonl",
    "rejected_rows.jsonl",
    "manifest.json",
    "statistics.json",
    "dataset_card.md",
    "validation_report.json",
    "validation_report.md",
    "provenance_report.json",
)


class CoverageError(ValueError):
    """Raised for malformed coverage or union inputs."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise CoverageError(f"input is missing or symlinked: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageError(f"invalid JSON input: {path}") from exc


def _load_jsonl(path: Path, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise CoverageError(f"JSONL input is missing or symlinked: {path}")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CoverageError(f"could not read JSONL input: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CoverageError(f"malformed JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise CoverageError(f"JSONL row is not an object: {path}:{line_number}")
        rows.append(value)
    if not rows and not allow_empty:
        raise CoverageError(f"JSONL input is empty: {path}")
    return rows


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise CoverageError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _validate_frozen_split(
    split_path: Path,
    *,
    expected_source_commit: str,
    expected_source_tree_sha256: str,
    expected_split_sha256: str,
    expected_train_count: int,
) -> tuple[dict[str, Any], list[str], list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    try:
        split = _load_json(split_path)
    except CoverageError as exc:
        return {}, [str(exc)], [], {}
    actual_split_sha256 = sha256_file(split_path)
    if actual_split_sha256 != expected_split_sha256:
        errors.append("frozen split SHA-256 mismatch")
    if split.get("schema_version") != "rtl_generation_split_v0.1":
        errors.append("frozen split has the wrong schema")
    counts = split.get("counts")
    if expected_train_count == EXPECTED_TRAIN_COUNT:
        if split.get("row_count") != 156:
            errors.append("frozen split row count is not 156")
        if counts != {"train": 111, "validation": 23, "test": 22}:
            errors.append("frozen split counts are not 111/23/22")
    elif not isinstance(counts, dict) or counts.get("train") != expected_train_count:
        errors.append("test split fixture does not match its expected train count")
    train_ids = split.get("splits", {}).get("train")
    validation_ids = split.get("splits", {}).get("validation")
    test_ids = split.get("splits", {}).get("test")
    if not isinstance(train_ids, list) or len(train_ids) != expected_train_count:
        errors.append("frozen train split is not an ordered 111-ID list")
        train_ids = []
    for label, values in (("validation", validation_ids), ("test", test_ids)):
        if not isinstance(values, list):
            errors.append(f"frozen {label} split is not an ID list")
    if len(train_ids) != len(set(train_ids)):
        errors.append("frozen train split contains duplicate source IDs")
    split_rows = split.get("rows")
    by_source: dict[str, dict[str, Any]] = {}
    if not isinstance(split_rows, list):
        errors.append("frozen split rows are not an array")
        split_rows = []
    for row in split_rows:
        if not isinstance(row, dict):
            errors.append("frozen split contains a non-object row")
            continue
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append("frozen split row has no source_id")
            continue
        if source_id in by_source:
            errors.append(f"frozen split duplicates source_id: {source_id}")
        by_source[source_id] = row
        if source_id in train_ids and row.get("split") != "train":
            errors.append(f"frozen train row has the wrong split: {source_id}")
    # The frozen split schema stores source provenance in the inventory-linked
    # rows rather than a top-level commit field.  The expected identities are
    # therefore checked by the caller against each generated package manifest.
    _ = expected_source_commit, expected_source_tree_sha256
    return split, sorted(set(errors)), list(train_ids), by_source


def _package_descriptor(
    package_dir: Path,
    *,
    expected_source_commit: str,
    expected_source_tree_sha256: str,
    expected_split_sha256: str,
    train_order: dict[str, int],
    split_by_source: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    try:
        if package_dir.is_symlink() or not package_dir.is_dir():
            raise CoverageError("package directory is missing or symlinked")
        manifest = _load_json(package_dir / "manifest.json")
        rows = _load_jsonl(package_dir / "all.jsonl")
        train_rows = _load_jsonl(package_dir / "train.jsonl")
        tree_hash = package_tree_sha256(package_dir)
    except (CoverageError, OSError, ValueError) as exc:
        return {
            "package_id": package_dir.name,
            "package_dir": _display_path(package_dir),
            "tree_sha256": None,
            "row_count": 0,
            "rejected_rows": None,
        }, [], [f"{package_dir.name}: {exc}"]
    if not isinstance(manifest, dict):
        errors.append(f"{package_dir.name}: manifest is not an object")
        manifest = {}
    package_id = manifest.get("package_id") or package_dir.name
    if not isinstance(package_id, str) or not package_id:
        errors.append(f"{package_dir.name}: package_id is missing")
        package_id = package_dir.name
    if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        errors.append(f"{package_id}: manifest schema mismatch")
    if manifest.get("source_commit") != expected_source_commit:
        errors.append(f"{package_id}: source commit mismatch")
    if manifest.get("source_tree_sha256") != expected_source_tree_sha256:
        errors.append(f"{package_id}: source tree SHA-256 mismatch")
    if manifest.get("frozen_split_sha256") != expected_split_sha256:
        errors.append(f"{package_id}: frozen split SHA-256 mismatch")
    if manifest.get("promotion_allowed") is not False:
        errors.append(f"{package_id}: promotion_allowed must be false")
    if manifest.get("all_rows") != len(rows) or manifest.get("train_rows") != len(train_rows):
        errors.append(f"{package_id}: manifest row counts do not match files")
    if len(rows) != len(train_rows):
        errors.append(f"{package_id}: all.jsonl and train.jsonl row counts differ")
    elif rows != train_rows:
        errors.append(f"{package_id}: train.jsonl is not byte-equivalent in row content to all.jsonl")
    local_sources: set[str] = set()
    local_tasks: set[str] = set()
    local_candidates: set[str] = set()
    for index, row in enumerate(rows, 1):
        errors.extend(validate_generation_sft_row(row, f"{package_id} row {index}"))
        source_id = row.get("source_id")
        task_id = row.get("task_id")
        candidate_id = row.get("candidate_id")
        if source_id in local_sources:
            errors.append(f"{package_id}: duplicate source_id {source_id}")
        if task_id in local_tasks:
            errors.append(f"{package_id}: duplicate task_id {task_id}")
        if candidate_id in local_candidates:
            errors.append(f"{package_id}: duplicate candidate_id {candidate_id}")
        local_sources.add(source_id)
        local_tasks.add(task_id)
        local_candidates.add(candidate_id)
        if source_id not in train_order:
            errors.append(f"{package_id}: row is outside frozen train split: {source_id}")
            continue
        split_row = split_by_source.get(source_id)
        if not isinstance(split_row, dict) or split_row.get("task_id") != task_id or split_row.get("split") != "train":
            errors.append(f"{package_id}: source/task identity does not match frozen split: {source_id}")
        verification = row.get("verification")
        if not isinstance(verification, dict) or verification.get("reference_supplied") is not False:
            errors.append(f"{package_id}: reference-supply policy is not false: {source_id}")
        if _contains_private_marker(row):
            errors.append(f"{package_id}: private content marker detected: {source_id}")
    descriptor = {
        "package_id": package_id,
        "package_dir": _display_path(package_dir),
        "tree_sha256": tree_hash,
        "row_count": len(rows),
        "rejected_rows": manifest.get("rejected_rows"),
        "source_ids": [row.get("source_id") for row in rows],
        "manifest_sha256": sha256_file(package_dir / "manifest.json"),
    }
    return descriptor, rows, sorted(set(errors))


def validate_train_coverage(
    package_dirs: Iterable[Path],
    split_path: Path,
    *,
    expected_source_commit: str = EXPECTED_SOURCE_COMMIT,
    expected_source_tree_sha256: str = EXPECTED_SOURCE_TREE_SHA256,
    expected_split_sha256: str = EXPECTED_FROZEN_SPLIT_SHA256,
    expected_train_count: int = EXPECTED_TRAIN_COUNT,
) -> tuple[dict[str, Any], int]:
    """Validate immutable packages and report train-source coverage."""
    package_paths = list(package_dirs)
    errors: list[str] = []
    split, split_errors, train_ids, split_by_source = _validate_frozen_split(
        split_path,
        expected_source_commit=expected_source_commit,
        expected_source_tree_sha256=expected_source_tree_sha256,
        expected_split_sha256=expected_split_sha256,
        expected_train_count=expected_train_count,
    )
    errors.extend(split_errors)
    train_order = {source_id: index for index, source_id in enumerate(train_ids)}
    descriptors: list[dict[str, Any]] = []
    covered_source_ids: list[str] = []
    covered_task_ids: list[str] = []
    covered_candidate_ids: list[str] = []
    package_ids: set[str] = set()
    for package_dir in package_paths:
        descriptor, rows, package_errors = _package_descriptor(
            package_dir,
            expected_source_commit=expected_source_commit,
            expected_source_tree_sha256=expected_source_tree_sha256,
            expected_split_sha256=expected_split_sha256,
            train_order=train_order,
            split_by_source=split_by_source,
        )
        descriptors.append({key: value for key, value in descriptor.items() if key != "source_ids"})
        errors.extend(package_errors)
        package_id = descriptor.get("package_id")
        if package_id in package_ids:
            errors.append(f"duplicate package_id: {package_id}")
        package_ids.add(package_id)
        covered_source_ids.extend(row.get("source_id") for row in rows)
        covered_task_ids.extend(row.get("task_id") for row in rows)
        covered_candidate_ids.extend(row.get("candidate_id") for row in rows)
    duplicate_source_ids = sorted({value for value in covered_source_ids if covered_source_ids.count(value) > 1})
    duplicate_task_ids = sorted({value for value in covered_task_ids if covered_task_ids.count(value) > 1})
    duplicate_candidate_ids = sorted({value for value in covered_candidate_ids if covered_candidate_ids.count(value) > 1})
    if duplicate_source_ids:
        errors.append("duplicate source IDs occur across packages")
    if duplicate_task_ids:
        errors.append("duplicate task IDs occur across packages")
    if duplicate_candidate_ids:
        errors.append("duplicate candidate IDs occur across packages")
    covered_set = set(covered_source_ids)
    unexpected = sorted(covered_set - set(train_ids), key=lambda value: (train_order.get(value, len(train_order)), value))
    if unexpected:
        errors.append("coverage contains source IDs outside frozen train split")
    ordered_covered = [source_id for source_id in train_ids if source_id in covered_set]
    remaining = [source_id for source_id in train_ids if source_id not in covered_set]
    if len(covered_set) > expected_train_count:
        errors.append("covered source count exceeds frozen train count")
    report = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "frozen_split_sha256": expected_split_sha256,
        "split_path": _display_path(split_path),
        "expected_train_count": expected_train_count,
        "covered_train_count": len(covered_set),
        "remaining_train_count": len(remaining),
        "package_count": len(package_paths),
        "packages": descriptors,
        "covered_source_ids": ordered_covered,
        "remaining_source_ids": remaining,
        "unexpected_source_ids": unexpected,
        "duplicate_source_ids": duplicate_source_ids,
        "duplicate_task_ids": duplicate_task_ids,
        "duplicate_candidate_ids": duplicate_candidate_ids,
        "complete": not errors and len(covered_set) == expected_train_count and not remaining,
        "errors": sorted(set(errors)),
    }
    return report, 0 if not report["errors"] else 1


def _prepare_output_dir(output_dir: Path) -> None:
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise CoverageError("union output must be a regular directory")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise CoverageError("union output directory must be absent or empty")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = output_dir.stat()
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise CoverageError("union output directory must have mode 0700")


def assemble_train_union(
    package_dirs: Iterable[Path],
    split_path: Path,
    output_dir: Path,
    *,
    package_id: str,
    coverage_report_path: Path | None = None,
    expected_source_commit: str = EXPECTED_SOURCE_COMMIT,
    expected_source_tree_sha256: str = EXPECTED_SOURCE_TREE_SHA256,
    expected_split_sha256: str = EXPECTED_FROZEN_SPLIT_SHA256,
    expected_train_count: int = EXPECTED_TRAIN_COUNT,
) -> tuple[dict[str, Any], int]:
    """Build a new immutable union package from verified package rows."""
    errors: list[str] = []
    package_paths = list(package_dirs)
    coverage_report, coverage_code = validate_train_coverage(
        package_paths,
        split_path,
        expected_source_commit=expected_source_commit,
        expected_source_tree_sha256=expected_source_tree_sha256,
        expected_split_sha256=expected_split_sha256,
        expected_train_count=expected_train_count,
    )
    errors.extend(coverage_report.get("errors", []))
    if coverage_code or not coverage_report.get("complete"):
        return {"ok": False, "published": False, "coverage": coverage_report, "errors": sorted(set(errors))}, 1
    if coverage_report_path is not None:
        try:
            if _load_json(coverage_report_path) != coverage_report:
                errors.append("supplied coverage report does not match recomputed coverage")
        except CoverageError as exc:
            errors.append(str(exc))
    if errors:
        return {"ok": False, "published": False, "coverage": coverage_report, "errors": sorted(set(errors))}, 1

    try:
        _prepare_output_dir(output_dir)
    except CoverageError as exc:
        return {"ok": False, "errors": [str(exc)], "published": False}, 1

    split, split_errors, train_ids, split_by_source = _validate_frozen_split(
        split_path,
        expected_source_commit=expected_source_commit,
        expected_source_tree_sha256=expected_source_tree_sha256,
        expected_split_sha256=expected_split_sha256,
        expected_train_count=expected_train_count,
    )
    errors.extend(split_errors)
    rows_by_source: dict[str, dict[str, Any]] = {}
    for package_dir in package_paths:
        for row in _load_jsonl(package_dir / "all.jsonl"):
            rows_by_source[row["source_id"]] = row
    ordered_rows = [rows_by_source[source_id] for source_id in train_ids]
    if len(ordered_rows) != expected_train_count:
        errors.append("ordered union does not contain the expected train count")
    if errors:
        return {"ok": False, "published": False, "coverage": coverage_report, "errors": sorted(set(errors))}, 1

    package_descriptors = coverage_report["packages"]
    manifest = {
        # Keep the established package schema so the existing package
        # validator remains authoritative; the union-specific schema is an
        # additive manifest field rather than a dataset-schema fork.
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "union_schema_version": UNION_MANIFEST_SCHEMA_VERSION,
        "package_id": package_id,
        "dataset_name": "verilog_eval_verified_generation_v0_1",
        "dataset_version": "train_v001",
        "dataset_stage": "verified_generation_sft_union",
        "all_rows": len(ordered_rows),
        "train_rows": len(ordered_rows),
        "validation_rows": 0,
        "test_rows": 0,
        "rejected_rows": 0,
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "frozen_split_sha256": expected_split_sha256,
        "source_packages": deepcopy(package_descriptors),
        "coverage_report_sha256": sha256_file(coverage_report_path) if coverage_report_path is not None else None,
        "promotion_allowed": False,
        "review_status": "automated_verified_unreviewed",
        "approval_status": "not_approved",
        "training_allowed": True,
        "training_scope": "experimental",
        "reference_supplied": False,
        "created_by": "assemble_rtl_generation_train_release",
    }
    statistics = {
        "schema_version": "rtl_generation_train_union_statistics_v0.1",
        "package_id": package_id,
        "all_rows": len(ordered_rows),
        "train_rows": len(ordered_rows),
        "validation_rows": 0,
        "test_rows": 0,
        "rejected_rows": 0,
        "source_package_count": len(package_descriptors),
        "source_ids_in_frozen_order": [row["source_id"] for row in ordered_rows],
        "promotion_allowed": False,
        "training_allowed": True,
        "training_scope": "experimental",
    }
    dataset_card = (
        f"# {package_id}\n\n"
        "This is a train-only union of immutable, executable-verified RTL generation packages. "
        "It is intended for experimental distillation only.\n\n"
        f"Rows: {len(ordered_rows)}\n"
        f"Source commit: `{expected_source_commit}`\n"
        f"Frozen split SHA-256: `{expected_split_sha256}`\n"
        "Validation and test rows: 0\n"
        "Promotion allowed: false\n"
    )
    for name, content in (
        ("all.jsonl", _jsonl_bytes(ordered_rows)),
        ("train.jsonl", _jsonl_bytes(ordered_rows)),
        ("rejected_rows.jsonl", b""),
        ("manifest.json", _json_bytes(manifest)),
        ("statistics.json", _json_bytes(statistics)),
        ("dataset_card.md", dataset_card.encode("utf-8")),
    ):
        _write_exclusive(output_dir / name, content)

    from scripts.dataset.rtl_generation_dataset import validate_generation_sft_package

    package_report, package_code = validate_generation_sft_package(output_dir, require_reports=False)
    validation_report = {
        "schema_version": "rtl_generation_train_union_validation_v0.1",
        "package_id": package_id,
        "ok": package_code == 0 and not errors,
        "row_count": len(ordered_rows),
        "train_count": len(ordered_rows),
        "rejected_count": 0,
        "coverage_complete": True,
        "package_validator": package_report,
        "qualification_and_execution_are_inherited": True,
        "promotion_allowed": False,
        "errors": sorted(set(errors + package_report.get("errors", []))),
    }
    provenance_report = {
        "schema_version": "rtl_generation_train_union_provenance_v0.1",
        "package_id": package_id,
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "frozen_split_sha256": expected_split_sha256,
        "source_packages": package_descriptors,
        "coverage_report_sha256": sha256_file(coverage_report_path) if coverage_report_path is not None else None,
        "reference_supplied": False,
        "private_content_detected": False,
        "promotion_allowed": False,
    }
    validation_md = (
        f"# Validation report: {package_id}\n\n"
        f"- OK: `{str(validation_report['ok']).lower()}`\n"
        f"- Rows: `{len(ordered_rows)}`\n"
        "- Train rows: `111`\n"
        "- Validation/test rows: `0`\n"
        "- Promotion allowed: `false`\n"
    )
    _write_exclusive(output_dir / "validation_report.json", _json_bytes(validation_report))
    _write_exclusive(output_dir / "validation_report.md", validation_md.encode("utf-8"))
    _write_exclusive(output_dir / "provenance_report.json", _json_bytes(provenance_report))
    final_report, final_code = validate_generation_sft_package(output_dir)
    result = {
        "ok": final_code == 0 and validation_report["ok"],
        "published": True,
        "package_id": package_id,
        "output_dir": _display_path(output_dir),
        "row_count": len(ordered_rows),
        "train_count": len(ordered_rows),
        "rejected_count": 0,
        "coverage": coverage_report,
        "validation": final_report,
        "package_tree_sha256": package_tree_sha256(output_dir),
        "errors": sorted(set(errors + final_report.get("errors", []))),
    }
    if result["errors"]:
        result["ok"] = False
    return result, 0 if result["ok"] else 1


def _coverage_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--package-dir", required=True, action="append", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-source-commit", default=EXPECTED_SOURCE_COMMIT)
    parser.add_argument("--expected-source-tree-sha256", default=EXPECTED_SOURCE_TREE_SHA256)
    parser.add_argument("--expected-split-sha256", default=EXPECTED_FROZEN_SPLIT_SHA256)
    parser.add_argument("--expected-train-count", type=int, default=EXPECTED_TRAIN_COUNT)
    parser.add_argument("--json", action="store_true")
    return parser


def coverage_main(argv: list[str] | None = None) -> int:
    args = _coverage_parser().parse_args(argv)
    try:
        report, code = validate_train_coverage(
            args.package_dir,
            args.split,
            expected_source_commit=args.expected_source_commit,
            expected_source_tree_sha256=args.expected_source_tree_sha256,
            expected_split_sha256=args.expected_split_sha256,
            expected_train_count=args.expected_train_count,
        )
        if args.output is not None:
            _write_exclusive(args.output, _json_bytes(report))
    except (CoverageError, OSError, ValueError) as exc:
        report, code = {"ok": False, "complete": False, "errors": [str(exc)]}, 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


def _assemble_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--package-dir", required=True, action="append", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-dir", type=Path)
    parser.add_argument("--coverage-report", type=Path)
    parser.add_argument("--package-id", default="verilog_eval_generation_train_v001_complete")
    parser.add_argument("--expected-source-commit", default=EXPECTED_SOURCE_COMMIT)
    parser.add_argument("--expected-source-tree-sha256", default=EXPECTED_SOURCE_TREE_SHA256)
    parser.add_argument("--expected-split-sha256", default=EXPECTED_FROZEN_SPLIT_SHA256)
    parser.add_argument("--expected-train-count", type=int, default=EXPECTED_TRAIN_COUNT)
    parser.add_argument("--json", action="store_true")
    return parser


def assemble_main(argv: list[str] | None = None) -> int:
    args = _assemble_parser().parse_args(argv)
    if args.preflight_only:
        if args.output_dir is not None or args.preflight_dir is None:
            _assemble_parser().error("--preflight-only requires --preflight-dir and forbids --output-dir")
        output_dir = args.preflight_dir
        canonical = (Path.cwd() / "data" / "distill").resolve()
        resolved = output_dir.resolve()
        if resolved == canonical or canonical in resolved.parents:
            _assemble_parser().error("preflight output must not be under data/distill")
    else:
        if args.output_dir is None:
            _assemble_parser().error("--output-dir is required unless --preflight-only is used")
        output_dir = args.output_dir
    try:
        result, code = assemble_train_union(
            args.package_dir,
            args.split,
            output_dir,
            package_id=args.package_id,
            coverage_report_path=args.coverage_report,
            expected_source_commit=args.expected_source_commit,
            expected_source_tree_sha256=args.expected_source_tree_sha256,
            expected_split_sha256=args.expected_split_sha256,
            expected_train_count=args.expected_train_count,
        )
        if args.preflight_only:
            result["preflight_only"] = True
            result["canonical_publication"] = False
            result["consumable"] = False
    except (CoverageError, OSError, ValueError) as exc:
        result, code = {"ok": False, "published": False, "errors": [str(exc)]}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


__all__ = [
    "COVERAGE_SCHEMA_VERSION",
    "EXPECTED_FROZEN_SPLIT_SHA256",
    "EXPECTED_SOURCE_COMMIT",
    "EXPECTED_SOURCE_TREE_SHA256",
    "EXPECTED_TRAIN_COUNT",
    "UNION_MANIFEST_SCHEMA_VERSION",
    "assemble_train_union",
    "assemble_main",
    "coverage_main",
    "package_tree_sha256",
    "sha256_file",
    "validate_train_coverage",
]
