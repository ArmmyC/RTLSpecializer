"""Acquisition, complete inventory, and frozen split controls for RTL data.

This module only reads local source metadata and file bytes for hashing.  It
never returns or serializes reference RTL, testbench text, support-file text,
or absolute source paths.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

from scripts.dataset.rtl_generation_preparation import (
    SourceRow,
    _json_bytes,
    _license_is_usable,
    _readiness,
    _relative_display,
    _sha256,
    _task_id,
    _text_bytes,
    _verification_dependency_report,
    discover_source_rows,
)
from scripts.dataset.split_dataset import ratios_valid, split_rows


ACQUISITION_SCHEMA_VERSION = "rtl_source_acquisition_v0.1"
INVENTORY_SCHEMA_VERSION = "rtl_source_inventory_row_v0.1"
SPLIT_SCHEMA_VERSION = "rtl_generation_split_v0.1"
MISSING_REPORT_SCHEMA_VERSION = "rtl_source_missing_report_v0.1"
DUPLICATE_REPORT_SCHEMA_VERSION = "rtl_source_duplicate_report_v0.1"
LICENSE_REPORT_SCHEMA_VERSION = "rtl_source_license_report_v0.1"
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SPLIT_NAMES = ("train", "validation", "test")
BEHAVIOR_CATEGORIES = (
    "combinational",
    "sequential",
    "counter_or_timer",
    "fsm",
    "reset_sensitive",
)


def _error_path(path: Path) -> str:
    """Return a stable path label without exposing absolute paths."""
    try:
        return _relative_display(path)
    except Exception:
        return path.name


def _validate_source_commit(source_commit: str) -> list[str]:
    if not isinstance(source_commit, str) or not SOURCE_COMMIT_RE.fullmatch(source_commit):
        return ["source_commit must be exactly 40 lowercase hexadecimal characters"]
    return []


def _safe_tree_entries(root: Path) -> tuple[list[tuple[str, int, int, str]], int, list[str]]:
    """Hash a source tree without following links or including VCS internals."""
    errors: list[str] = []
    symlink_count = 0
    entries: list[tuple[str, int, int, str]] = []

    if root.is_symlink():
        return [], 1, ["source root must not be a symlink"]
    if not root.exists() or not root.is_dir():
        return [], 0, ["source root must be an existing directory"]

    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        try:
            metadata = path.lstat()
        except OSError as exc:
            errors.append(f"could not stat source entry {relative}: {exc}")
            continue
        if path.is_symlink():
            symlink_count += 1
            errors.append(f"source tree contains a symlink: {relative}")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            errors.append(f"source tree contains a special file: {relative}")
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            errors.append(f"could not read source entry {relative}: {exc}")
            continue
        entries.append((relative, int(metadata.st_mode & 0o777), len(content), hashlib.sha256(content).hexdigest()))
    return entries, symlink_count, sorted(set(errors))


def source_tree_sha256(root: Path) -> tuple[str | None, int, list[str]]:
    entries, symlink_count, errors = _safe_tree_entries(root)
    if errors:
        return None, symlink_count, errors
    digest = hashlib.sha256()
    for relative, mode, size, content_hash in entries:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{mode:04o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), symlink_count, []


def _git_state(root: Path) -> tuple[bool | None, list[str]]:
    git_marker = root / ".git"
    if not git_marker.exists():
        return None, []
    completed = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None, ["could not inspect source checkout state"]
    return bool(completed.stdout.strip()), []


def _license_files(root: Path) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    found: list[dict[str, str]] = []
    candidates = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if path.name.casefold().startswith(("license", "copying")):
            candidates.append(path)
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            errors.append(f"license file must be a regular file: {path.name}")
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:
            errors.append(f"could not read license file {path.name}: {exc}")
            continue
        found.append({"path": path.name, "sha256": hashlib.sha256(content).hexdigest()})
    if not found:
        errors.append("no root-level LICENSE or COPYING file found")
    return found, errors


def build_acquisition_manifest(
    source_root: Path,
    input_path: Path,
    source_commit: str,
    *,
    dataset_name: str = "VerilogEval",
    repository: str = "NVlabs/verilog-eval",
    row_count: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    errors = _validate_source_commit(source_commit)
    tree_hash, symlink_count, tree_errors = source_tree_sha256(source_root)
    errors.extend(tree_errors)
    dirty, git_errors = _git_state(source_root)
    errors.extend(git_errors)
    license_files, license_errors = _license_files(source_root) if source_root.exists() else ([], ["source root does not exist"])
    errors.extend(license_errors)
    if dirty:
        errors.append("source checkout is dirty")
    manifest = {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "dataset_name": dataset_name,
        "repository": repository,
        "source_commit": source_commit,
        "acquired_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "local_root": _error_path(source_root),
        "input": _error_path(input_path),
        "license_files": license_files,
        "source_tree_sha256": tree_hash,
        "dirty_checkout": dirty,
        "symlink_count": symlink_count,
        "row_count": row_count,
        "errors": sorted(set(errors)),
    }
    return manifest, sorted(set(errors))


def _source_identity(input_path: Path, source_root: Path, source_id: str) -> str:
    try:
        relative_input = input_path.resolve().relative_to(source_root.resolve()).as_posix()
    except (OSError, ValueError):
        relative_input = input_path.name
    if relative_input == ".":
        return source_id
    return f"{relative_input}/{source_id}"


def _apply_source_commit(rows: list[SourceRow], source_commit: str) -> list[str]:
    errors = _validate_source_commit(source_commit)
    if errors:
        return errors
    for row in rows:
        existing = row.provenance.get("source_commit") if isinstance(row.provenance, dict) else None
        if existing not in (None, "", source_commit):
            errors.append(f"source commit mismatch for source_id {row.source_id}")
        row.source_commit = source_commit
        row.provenance["source_commit"] = source_commit
    return sorted(set(errors))


def _inventory_row(row: SourceRow, input_path: Path, source_root: Path, duplicate_group: str | None) -> dict[str, Any]:
    readiness, readiness_reasons = _readiness(row)
    try:
        dependency = _verification_dependency_report(row)
        unresolved_modules = len(dependency.unresolved_modules)
        unresolved_packages = len(dependency.unresolved_packages)
        dependency_ambiguous = bool(dependency.ambiguous)
        tb_top_count = dependency.probable_instantiations.count(row.top_module_hint or "")
    except Exception:
        unresolved_modules = None
        unresolved_packages = None
        dependency_ambiguous = True
        tb_top_count = None

    support_hashes = [
        {"path": name, "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(row.support_files.items())
    ]
    missing_files: list[str] = []
    if not row.specification:
        missing_files.append("prompt")
    if not row.reference_rtl:
        missing_files.append("reference_rtl")
    if not row.testbench:
        missing_files.append("testbench")
    if not _license_is_usable(row.license):
        missing_files.append("license_metadata")

    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_id": row.source_id,
        "source_dataset": row.source_dataset,
        "source_commit": row.source_commit or row.provenance.get("source_commit"),
        "license": row.license,
        "design_family": row.design_family,
        "task_id": _task_id(row),
        "source_prompt_sha256": _sha256(_text_bytes(row.specification)),
        "reference_rtl_sha256": _sha256(_text_bytes(row.reference_rtl)) if row.reference_rtl is not None else None,
        "testbench_sha256": _sha256(_text_bytes(row.testbench)) if row.testbench is not None else None,
        "support_file_hashes": support_hashes,
        "top_module": row.top_module_hint,
        "interface_deterministic": bool(row.top_module_hint and row.interface_hints),
        "clock_signal_count": len(row.clock_hints),
        "reset_signal_count": len(row.reset_hints),
        "behavior_categories": _behavior_categories(row),
        "verification_readiness": readiness,
        "readiness_reasons": readiness_reasons,
        "duplicate_group": duplicate_group,
        "missing_files": missing_files,
        "source_path_identity": _source_identity(input_path, source_root, row.source_id),
        "unresolved_dependency_count": unresolved_modules,
        "unresolved_package_count": unresolved_packages,
        "dependency_analysis_ambiguous": dependency_ambiguous,
        "testbench_top_module_count": tb_top_count,
        "warning_codes": sorted(set(row.warnings)),
    }


def _behavior_categories(row: SourceRow) -> list[str]:
    """Classify observable source metadata for deterministic smoke selection."""
    family = str(row.design_family or "").casefold()
    categories = ["sequential" if row.clock_hints else "combinational"]
    if any(token in family for token in ("counter", "timer")):
        categories.append("counter_or_timer")
    if "fsm" in family or "state" in family:
        categories.append("fsm")
    if row.reset_hints:
        categories.append("reset_sensitive")
    return [category for category in BEHAVIOR_CATEGORIES if category in categories]


def build_auxiliary_inventory_reports(
    report: dict[str, Any],
    inventory_rows: list[dict[str, Any]],
    acquisition: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build deterministic derived reports without source text or absolute paths."""
    missing_rows = [
        {
            "source_id": row["source_id"],
            "task_id": row["task_id"],
            "verification_readiness": row["verification_readiness"],
            "missing_files": row["missing_files"],
            "readiness_reasons": row["readiness_reasons"],
        }
        for row in inventory_rows
        if row["missing_files"]
    ]
    duplicate_counts = Counter(row["source_id"] for row in inventory_rows)
    duplicate_rows = []
    for source_id in sorted(source_id for source_id, count in duplicate_counts.items() if count > 1):
        matches = [row for row in inventory_rows if row["source_id"] == source_id]
        duplicate_rows.append({
            "source_id": source_id,
            "count": len(matches),
            "task_ids": sorted(row["task_id"] for row in matches),
            "source_path_identities": sorted(row["source_path_identity"] for row in matches),
        })
    license_rows = [
        {
            "source_id": row["source_id"],
            "task_id": row["task_id"],
            "license": row["license"],
            "license_usable": "license_metadata" not in row["missing_files"],
            "verification_readiness": row["verification_readiness"],
        }
        for row in inventory_rows
    ]
    usable_license_count = sum(1 for row in license_rows if row["license_usable"])
    return {
        "missing": {
            "schema_version": MISSING_REPORT_SCHEMA_VERSION,
            "source_commit": report.get("source_commit"),
            "total_rows": len(inventory_rows),
            "rows_with_missing_files": len(missing_rows),
            "missing_file_counts": dict(sorted(
                Counter(
                    missing_file
                    for row in missing_rows
                    for missing_file in row["missing_files"]
                ).items()
            )),
            "rows": missing_rows,
        },
        "duplicates": {
            "schema_version": DUPLICATE_REPORT_SCHEMA_VERSION,
            "source_commit": report.get("source_commit"),
            "duplicate_source_id_count": len(duplicate_rows),
            "rows": duplicate_rows,
        },
        "license": {
            "schema_version": LICENSE_REPORT_SCHEMA_VERSION,
            "source_commit": report.get("source_commit"),
            "license_files": acquisition.get("license_files", []),
            "rows_with_usable_license": usable_license_count,
            "rows_with_unusable_or_missing_license": len(license_rows) - usable_license_count,
            "rows": license_rows,
        },
    }


def audit_source_inventory(
    input_path: Path,
    source_root: Path,
    source_commit: str,
    *,
    dataset_name: str = "VerilogEval",
    repository: str = "NVlabs/verilog-eval",
    correction_manifest: Path | None = None,
    correction_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], int]:
    rows, discovery_errors = discover_source_rows(input_path)
    commit_errors = _apply_source_commit(rows, source_commit)
    errors = sorted(set(discovery_errors + commit_errors))
    correction_metadata: dict[str, Any] | None = None
    if (correction_manifest is None) != (correction_root is None):
        errors.append("correction_manifest and correction_root must be supplied together")
    elif correction_manifest is not None and correction_root is not None:
        from scripts.dataset.rtl_generation_asset_corrections import (
            overlay_source_rows,
            sha256_file,
        )

        rows, correction_errors, correction_rows = overlay_source_rows(
            rows,
            correction_manifest,
            correction_root,
        )
        errors.extend(correction_errors)
        correction_metadata = {
            "manifest_sha256": sha256_file(correction_manifest) if correction_manifest.is_file() else None,
            "correction_root": _error_path(correction_root),
            "row_count": len(correction_rows),
            "source_ids": sorted(correction_rows),
            "version": "assetfix_v002",
        }
    try:
        input_path.resolve().relative_to(source_root.resolve())
    except (OSError, ValueError):
        errors.append("input path must be beneath source root")
    counts = Counter()
    families = Counter()
    duplicate_ids = {source_id for source_id, count in Counter(row.source_id for row in rows).items() if count > 1}
    inventory_rows: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (item.source_id, item.source_dataset)):
        duplicate_group = row.source_id if row.source_id in duplicate_ids else None
        inventory = _inventory_row(row, input_path, source_root, duplicate_group)
        inventory_rows.append(inventory)
        counts[inventory["verification_readiness"]] += 1
        families[inventory["design_family"]] += 1
    if duplicate_ids:
        errors.extend(f"duplicate source_id: {source_id}" for source_id in sorted(duplicate_ids))
    acquisition, acquisition_errors = build_acquisition_manifest(
        source_root,
        input_path,
        source_commit,
        dataset_name=dataset_name,
        repository=repository,
        row_count=len(inventory_rows),
    )
    errors.extend(acquisition_errors)
    report = {
        "schema_version": "rtl_generation_source_audit_v0.2",
        "input": _error_path(input_path),
        "source_root": _error_path(source_root),
        "source_commit": source_commit,
        "total_rows": len(inventory_rows),
        "unique_source_ids": len({row["source_id"] for row in inventory_rows}),
        "duplicate_source_id_count": len(duplicate_ids),
        "readiness_categories": dict(sorted(counts.items())),
        "design_family_distribution": dict(sorted(families.items())),
        "rows_appearing_executable_ready": counts["executable_ready"],
        "rows_with_confirmed_license_metadata": sum(_license_is_usable(row["license"]) for row in inventory_rows),
        "rows_with_placeholder_or_missing_license": sum(not _license_is_usable(row["license"]) for row in inventory_rows),
        "source_tree_sha256": acquisition.get("source_tree_sha256"),
        "correction_overlay": correction_metadata,
        "errors": sorted(set(errors)),
    }
    code = 0 if inventory_rows and not errors else 1
    return report, inventory_rows, acquisition, code


def _write_atomic(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"output must not be a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(f"temporary output already exists: {temporary}")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_inventory_outputs(
    report: dict[str, Any],
    inventory_rows: list[dict[str, Any]],
    acquisition: dict[str, Any],
    inventory_path: Path,
    acquisition_path: Path,
    *,
    audit_path: Path | None = None,
    missing_path: Path | None = None,
    duplicates_path: Path | None = None,
    license_path: Path | None = None,
) -> None:
    inventory_bytes = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in inventory_rows
    )
    _write_atomic(inventory_path, inventory_bytes)
    _write_atomic(acquisition_path, (json.dumps(acquisition, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if audit_path is not None:
        _write_atomic(audit_path, (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    auxiliary = build_auxiliary_inventory_reports(report, inventory_rows, acquisition)
    inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
    for value in auxiliary.values():
        value["inventory_sha256"] = inventory_hash
    if missing_path is not None:
        _write_atomic(missing_path, (json.dumps(auxiliary["missing"], ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if duplicates_path is not None:
        _write_atomic(duplicates_path, (json.dumps(auxiliary["duplicates"], ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if license_path is not None:
        _write_atomic(license_path, (json.dumps(auxiliary["license"], ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_inventory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("schema_version") != INVENTORY_SCHEMA_VERSION:
            raise ValueError(f"invalid inventory row at line {index}")
        rows.append(value)
    if not rows:
        raise ValueError("inventory is empty")
    return rows


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_manifest_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLIT_NAMES}
    for row in rows:
        split = row.get("split")
        if split not in result:
            raise ValueError(f"invalid split value: {split!r}")
        result[split].append(row)
    return result


def freeze_generation_split(
    inventory_path: Path,
    output_path: Path,
    *,
    seed: int = 7,
    ratios: tuple[float, float, float] = (0.70, 0.15, 0.15),
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    if not ratios_valid(*ratios):
        errors.append("split ratios must be non-negative and sum to 1.0")
    try:
        inventory = _load_inventory(inventory_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    source_ids = [str(row.get("source_id")) for row in inventory]
    if len(source_ids) != len(set(source_ids)):
        errors.append("inventory contains duplicate source IDs")
    if any(not row.get("source_id") for row in inventory):
        errors.append("inventory contains an empty source ID")
    if errors:
        return {"ok": False, "inventory": _error_path(inventory_path), "errors": sorted(set(errors))}, 1

    split_input = [
        {
            "source_id": row["source_id"],
            "task_id": row.get("task_id"),
            "design_family": row.get("design_family") or "unknown",
            "verification_readiness": row.get("verification_readiness"),
        }
        for row in inventory
    ]
    assigned = split_rows(split_input, ratios, seed, allow_family_overlap=False)
    split_rows_by_name = {
        "train": [{"source_id": row["source_id"], "task_id": row.get("task_id"), "design_family": row.get("design_family"), "verification_readiness": row.get("verification_readiness"), "split": "train"} for row in assigned["train"]],
        "validation": [{"source_id": row["source_id"], "task_id": row.get("task_id"), "design_family": row.get("design_family"), "verification_readiness": row.get("verification_readiness"), "split": "validation"} for row in assigned["val"]],
        "test": [{"source_id": row["source_id"], "task_id": row.get("task_id"), "design_family": row.get("design_family"), "verification_readiness": row.get("verification_readiness"), "split": "test"} for row in assigned["test"]],
    }
    flat = [row for rows in split_rows_by_name.values() for row in rows]
    assigned_ids = [row["source_id"] for row in flat]
    if sorted(assigned_ids) != sorted(source_ids):
        errors.append("split does not cover inventory exactly")
    family_sets = {
        name: {row["design_family"] for row in rows}
        for name, rows in split_rows_by_name.items()
    }
    for left_index, left in enumerate(SPLIT_NAMES):
        for right in SPLIT_NAMES[left_index + 1:]:
            overlap = family_sets[left] & family_sets[right]
            if overlap:
                errors.append(f"design-family overlap between {left} and {right}: {sorted(overlap)}")

    eligible_ids_by_split = {
        name: [
            row["source_id"]
            for row in split_rows_by_name[name]
            if row.get("verification_readiness") == "executable_ready"
        ]
        for name in SPLIT_NAMES
    }
    eligible_total = sum(len(values) for values in eligible_ids_by_split.values())
    eligible_ratios = {
        name: (len(eligible_ids_by_split[name]) / eligible_total if eligible_total else 0.0)
        for name in SPLIT_NAMES
    }

    manifest = {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "inventory_sha256": _sha256_file(inventory_path),
        "inventory_path": _error_path(inventory_path),
        "algorithm": "family_isolated_random_v1",
        "seed": seed,
        "ratios": {"train": ratios[0], "validation": ratios[1], "test": ratios[2]},
        "row_count": len(inventory),
        "eligible_generation_policy": {
            "split": "train",
            "verification_readiness": "executable_ready",
            "normalization_validation": "required",
            "teacher_generation": "train_only",
        },
        "eligible_generation_pool": {
            "source_ids": eligible_ids_by_split,
            "counts": {name: len(values) for name, values in eligible_ids_by_split.items()},
            "ratios": eligible_ratios,
            "total": eligible_total,
        },
        "splits": {name: [row["source_id"] for row in rows] for name, rows in split_rows_by_name.items()},
        "rows": flat,
        "counts": {name: len(rows) for name, rows in split_rows_by_name.items()},
        "errors": sorted(set(errors)),
    }
    if errors:
        return manifest, 1
    _write_atomic(output_path, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {**manifest, "ok": True, "output": _error_path(output_path)}, 0


def validate_generation_split(
    inventory_path: Path,
    split_path: Path,
    *,
    expected_seed: int | None = None,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    try:
        inventory = _load_inventory(inventory_path)
        manifest = _load_json(split_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    if manifest.get("schema_version") != SPLIT_SCHEMA_VERSION:
        errors.append("wrong split schema version")
    if manifest.get("inventory_sha256") != _sha256_file(inventory_path):
        errors.append("split inventory hash mismatch")
    if expected_seed is not None and manifest.get("seed") != expected_seed:
        errors.append("split seed mismatch")
    split_values = manifest.get("splits")
    if not isinstance(split_values, dict) or set(split_values) != set(SPLIT_NAMES):
        errors.append("split manifest must contain train, validation, and test IDs")
        split_values = {name: [] for name in SPLIT_NAMES}
    inventory_by_id = {row["source_id"]: row for row in inventory}
    all_ids = [source_id for name in SPLIT_NAMES for source_id in split_values.get(name, [])]
    if len(all_ids) != len(set(all_ids)):
        errors.append("split IDs overlap or are duplicated")
    if set(all_ids) != set(inventory_by_id):
        errors.append("split IDs do not exactly cover inventory IDs")
    family_sets: dict[str, set[str]] = {}
    for name in SPLIT_NAMES:
        family_sets[name] = {str(inventory_by_id[source_id].get("design_family")) for source_id in split_values.get(name, []) if source_id in inventory_by_id}
    for left_index, left in enumerate(SPLIT_NAMES):
        for right in SPLIT_NAMES[left_index + 1:]:
            if family_sets[left] & family_sets[right]:
                errors.append(f"design-family overlap between {left} and {right}")
    eligible_pool = manifest.get("eligible_generation_pool", {})
    eligible_ids = eligible_pool.get("source_ids", {}) if isinstance(eligible_pool, dict) else {}
    for name in SPLIT_NAMES:
        values = eligible_ids.get(name, []) if isinstance(eligible_ids, dict) else []
        if not isinstance(values, list) or any(source_id not in split_values.get(name, []) for source_id in values):
            errors.append(f"eligible generation IDs are not contained in split {name}")
        for source_id in values:
            if source_id in inventory_by_id and inventory_by_id[source_id].get("verification_readiness") != "executable_ready":
                errors.append(f"ineligible source ID in generation pool: {source_id}")
    report = {
        "ok": not errors,
        "inventory": _error_path(inventory_path),
        "split": _error_path(split_path),
        "inventory_rows": len(inventory),
        "split_counts": {name: len(split_values.get(name, [])) for name in SPLIT_NAMES},
        "errors": sorted(set(errors)),
    }
    return report, 0 if report["ok"] else 1


def load_split_source_ids(
    split_path: Path,
    *,
    split: str,
    inventory_path: Path | None = None,
    source_ids_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if split not in SPLIT_NAMES:
        return [], [f"invalid split: {split}"]
    try:
        manifest = _load_json(split_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [], [str(exc)]
    if manifest.get("schema_version") != SPLIT_SCHEMA_VERSION:
        errors.append("wrong split schema version")
    if inventory_path is not None:
        try:
            actual = _sha256_file(inventory_path)
        except OSError as exc:
            errors.append(f"could not hash inventory: {exc}")
        else:
            if manifest.get("inventory_sha256") != actual:
                errors.append("split inventory hash mismatch")
    split_ids = manifest.get("splits", {}).get(split, []) if isinstance(manifest.get("splits"), dict) else []
    if not isinstance(split_ids, list) or any(not isinstance(value, str) or not value for value in split_ids):
        errors.append(f"split {split} IDs are invalid")
        split_ids = []
    if len(split_ids) != len(set(split_ids)):
        errors.append(f"split {split} contains duplicate IDs")
    selected = list(split_ids)
    if source_ids_path is not None:
        try:
            requested = [line.strip() for line in source_ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeError) as exc:
            errors.append(f"could not read source-ID allowlist: {exc}")
            requested = []
        if len(requested) != len(set(requested)):
            errors.append("source-ID allowlist contains duplicates")
        missing = sorted(set(requested) - set(split_ids))
        if missing:
            errors.append(f"allowlist contains IDs outside split {split}: {missing}")
        selected = requested
    return selected, sorted(set(errors))


__all__ = [
    "ACQUISITION_SCHEMA_VERSION",
    "INVENTORY_SCHEMA_VERSION",
    "MISSING_REPORT_SCHEMA_VERSION",
    "DUPLICATE_REPORT_SCHEMA_VERSION",
    "LICENSE_REPORT_SCHEMA_VERSION",
    "SPLIT_SCHEMA_VERSION",
    "audit_source_inventory",
    "build_auxiliary_inventory_reports",
    "build_acquisition_manifest",
    "freeze_generation_split",
    "load_split_source_ids",
    "source_tree_sha256",
    "validate_generation_split",
    "write_inventory_outputs",
]
