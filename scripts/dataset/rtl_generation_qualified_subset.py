"""Bind passed qualification rows to a train-only normalization run.

This module is control-plane code only.  It reads qualification metadata and
hashes, derives a qualified source-ID allowlist, and reuses the existing
public/private normalization exporter.  It never calls a model and never
executes RTL, a testbench, or an EDA tool.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from scripts.dataset.rtl_generation_batch_selection import (
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    BATCH20_SOURCE_IDS,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
)
from scripts.dataset.rtl_generation_preparation import (
    _sha256,
    _task_id,
    _text_bytes,
    discover_source_rows,
    export_generation_normalization_batches,
)
from scripts.dataset.rtl_generation_inventory import source_tree_sha256


SCHEMA_VERSION = "rtl_generation_qualified_subset_binding_v0.1"
PREPARATION_SCHEMA_VERSION = "rtl_generation_qualified_normalization_preparation_v0.1"
CORRECTION_VERSION = "assetfix_v003"
CORRECTION_MANIFEST_SCHEMA_VERSION = "rtl_verification_asset_correction_v0.2"
CORRECTION_ROW_SCHEMA_VERSION = "rtl_verification_asset_correction_row_v0.2"
QUALIFICATION_REPORT_SCHEMA_VERSION = "rtl_asset_qualification_report_v0.1"
EXPECTED_QUALIFICATION_REPORT_SHA256 = "8b3ac80a6cea697b29fc5523529791811b62a6f035850083adbbbcf4ba3c7729"
EXPECTED_RAW_EVIDENCE_SHA256 = "954dbb606f721c568df58f9032ce63b72cd6e3e5edb302210cba67fa9d1e4eae"
EXPECTED_SIDECAR_SHA256 = "35c81fe662908ffb383991b0e505a3486330a9fe1cfaac53c6b07ec272f93e4f"
EXPECTED_QUALIFIED_LIST_SHA256 = "62c2216d3ed2b486f8540f6c25e321c52f960f702be12a001c0ebcf4b1c818e1"
EXPECTED_FAILED_LIST_SHA256 = "d53b327a37a78ec724e70f0cdc48f4fba069ba9f500726da10cb79612240cb72"
EXPECTED_CORRECTION_MANIFEST_SHA256 = "2b7cbe7a82f0b73e9960216254565600a9df46bebd66c71fa45f0f401501b8c1"
EXPECTED_SOURCE_IDS_SHA256 = "e1927846320a465a49e039e7a8f3518a12424d14e08972a10a1fd232a1d16bbd"


class QualifiedSubsetError(ValueError):
    """Raised when the qualified subset cannot be proven safe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise QualifiedSubsetError(f"required JSON input is not a regular file: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualifiedSubsetError(f"invalid JSON input: {path.name}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise QualifiedSubsetError(f"required JSONL input is not a regular file: {path.name}")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise QualifiedSubsetError(f"invalid JSONL row {path.name}:{index}") from exc
        if not isinstance(value, dict):
            raise QualifiedSubsetError(f"JSONL row is not an object: {path.name}:{index}")
        rows.append(value)
    return rows


def _write_exclusive(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise QualifiedSubsetError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, mode)


def _write_json(path: Path, value: Any) -> None:
    _write_exclusive(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )
    _write_exclusive(path, content)


def _read_ids(path: Path, label: str) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise QualifiedSubsetError(f"{label} is not a regular file")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise QualifiedSubsetError(f"{label} contains duplicate IDs")
    return values


def _read_optional_ids(path: Path, label: str) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise QualifiedSubsetError(f"{label} is not a regular file")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise QualifiedSubsetError(f"{label} contains duplicate IDs")
    return values


def _safe_testbench(correction_root: Path, row: dict[str, Any]) -> Path:
    relative = row.get("testbench_path")
    if not isinstance(relative, str) or not relative or relative.startswith("/") or "\\" in relative:
        raise QualifiedSubsetError(f"unsafe corrected testbench path: {row.get('source_id')}")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise QualifiedSubsetError(f"unsafe corrected testbench path: {row.get('source_id')}")
    path = correction_root.joinpath(*parts)
    if path.is_symlink() or not path.is_file():
        raise QualifiedSubsetError(f"corrected testbench is missing or symlinked: {row.get('source_id')}")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise QualifiedSubsetError(f"corrected testbench is not a unique regular file: {row.get('source_id')}")
    return path


def _assert_hash(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise QualifiedSubsetError(f"{label} hash mismatch")


def _validate_source_input_alignment(
    *,
    source_input: Path,
    source_root: Path,
    inventory: dict[str, dict[str, Any]],
    selected_ids: list[str],
) -> None:
    """Prove that the exported source view matches the pinned inventory."""

    if source_input.is_symlink() or not source_input.is_dir():
        raise QualifiedSubsetError("source input must be a regular directory")
    try:
        source_input.resolve().relative_to(source_root.resolve())
    except ValueError as exc:
        raise QualifiedSubsetError("source input must be beneath source root") from exc

    source_rows, discovery_errors = discover_source_rows(source_input)
    if discovery_errors:
        raise QualifiedSubsetError(
            "source input discovery failed: " + "; ".join(discovery_errors)
        )
    by_source_id = {row.source_id: row for row in source_rows}
    if len(by_source_id) != len(source_rows):
        raise QualifiedSubsetError("source input contains duplicate source IDs")

    for source_id in selected_ids:
        source_row = by_source_id.get(source_id)
        inventory_row = inventory.get(source_id)
        if source_row is None or inventory_row is None:
            raise QualifiedSubsetError(
                f"source input and inventory are missing selected source: {source_id}"
            )
        if source_row.source_dataset != inventory_row.get("source_dataset"):
            raise QualifiedSubsetError(f"source dataset mismatch: {source_id}")
        source_commit = inventory_row.get("source_commit")
        if not isinstance(source_commit, str) or not source_commit:
            raise QualifiedSubsetError(f"inventory source commit is missing: {source_id}")
        source_row.source_commit = source_commit
        source_row.provenance["source_commit"] = source_commit
        if _task_id(source_row) != inventory_row.get("task_id"):
            raise QualifiedSubsetError(f"source task identity mismatch: {source_id}")
        for label, value, expected in (
            (
                "prompt",
                _sha256(_text_bytes(source_row.specification)),
                inventory_row.get("source_prompt_sha256"),
            ),
            (
                "reference RTL",
                _sha256(_text_bytes(source_row.reference_rtl)),
                inventory_row.get("reference_rtl_sha256"),
            ),
            (
                "original testbench",
                _sha256(_text_bytes(source_row.testbench)),
                inventory_row.get("testbench_sha256"),
            ),
        ):
            if value != expected:
                raise QualifiedSubsetError(
                    f"source input {label} hash mismatch: {source_id}"
                )


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise QualifiedSubsetError(f"{label} must be a SHA-256 string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise QualifiedSubsetError(f"{label} must be a SHA-256 string") from exc
    return value


def _normalize_qualification_output_hashes(value: Any) -> dict[str, Any]:
    """Accept both qualification-output hash field generations.

    The qualification recorder's current schema uses explicit artifact names,
    while qualified-subset preparation historically consumed two shorter
    aliases.  Normalize those names only after proving that every supplied
    alias agrees, so downstream binding code can keep one canonical contract.
    """

    if not isinstance(value, dict):
        raise QualifiedSubsetError("qualification output hashes must be an object")
    normalized = dict(value)
    aliases = {
        "evidence_sha256": (
            "evidence_sha256",
            "raw_candidate_evidence_sha256",
            "runner_evidence_sha256",
        ),
        "qualification_validation_report_sha256": (
            "qualification_validation_report_sha256",
            "qualification_report_sha256",
        ),
        "qualification_evidence_sha256": ("qualification_evidence_sha256",),
        "runner_sidecar_sha256": ("runner_sidecar_sha256",),
        "qualified_task_ids_sha256": ("qualified_task_ids_sha256",),
        "failed_or_inconclusive_task_ids_sha256": (
            "failed_or_inconclusive_task_ids_sha256",
        ),
    }
    for canonical, names in aliases.items():
        supplied = {
            name: _require_sha256(value[name], f"qualification output hash: {name}")
            for name in names
            if value.get(name) is not None
        }
        if not supplied:
            raise QualifiedSubsetError(
                f"qualification output hash is missing: {canonical}"
            )
        if len(set(supplied.values())) != 1:
            raise QualifiedSubsetError(
                f"qualification output hash aliases disagree: {canonical}"
            )
        normalized[canonical] = next(iter(supplied.values()))
    return normalized


def _qualification_rows(
    *,
    qualification_report: dict[str, Any],
    selected_ids: list[str],
    qualified_task_ids: list[str],
    failed_task_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if qualification_report.get("schema_version") != QUALIFICATION_REPORT_SCHEMA_VERSION:
        raise QualifiedSubsetError("qualification report schema mismatch")
    if qualification_report.get("errors") != []:
        raise QualifiedSubsetError("qualification report contains errors")
    expected_count = len(selected_ids)
    if not selected_ids or expected_count != len(set(selected_ids)):
        raise QualifiedSubsetError("selection IDs must be non-empty and unique")
    if qualification_report.get("selected_tasks") != expected_count:
        raise QualifiedSubsetError("qualification report selected-task count mismatch")
    report_rows = qualification_report.get("rows")
    if not isinstance(report_rows, list) or len(report_rows) != expected_count:
        raise QualifiedSubsetError("qualification report row count does not match selection")
    by_source = {row.get("source_id"): row for row in report_rows}
    by_task = {row.get("task_id"): row for row in report_rows}
    if len(by_source) != expected_count or len(by_task) != expected_count:
        raise QualifiedSubsetError("qualification report contains duplicate identities")
    if [row.get("source_id") for row in report_rows] != selected_ids:
        raise QualifiedSubsetError("qualification report does not preserve the pinned selection order")
    ordered_rows = [by_source[source_id] for source_id in selected_ids]
    expected_task_ids = (
        [row["task_id"] for row in ordered_rows if row.get("qualification_passed") is True],
        [row["task_id"] for row in ordered_rows if row.get("qualification_passed") is not True],
    )
    expected_source_ids = (
        [row["source_id"] for row in ordered_rows if row.get("qualification_passed") is True],
        [row["source_id"] for row in ordered_rows if row.get("qualification_passed") is not True],
    )
    supplied_ids = (qualified_task_ids, failed_task_ids)
    if supplied_ids == expected_task_ids:
        identity_key = "task_id"
    elif supplied_ids == expected_source_ids:
        identity_key = "source_id"
    else:
        raise QualifiedSubsetError(
            "qualified task list is not the filtered pinned order"
        )
    if not qualified_task_ids:
        raise QualifiedSubsetError("qualified task list must contain at least one task")
    if len(qualified_task_ids) != len(set(qualified_task_ids)) or len(failed_task_ids) != len(set(failed_task_ids)):
        raise QualifiedSubsetError("qualified and failed task lists contain duplicates")
    if set(qualified_task_ids) & set(failed_task_ids):
        raise QualifiedSubsetError("qualified and failed task lists overlap")
    qualified_rows = []
    for identity in qualified_task_ids:
        row = by_task[identity] if identity_key == "task_id" else by_source[identity]
        if row.get("qualification_status") != "qualified":
            raise QualifiedSubsetError(f"qualified task has non-qualified status: {identity}")
        qualified_rows.append(row)
    failed_rows = [
        by_task[identity] if identity_key == "task_id" else by_source[identity]
        for identity in failed_task_ids
    ]
    return qualified_rows, failed_rows


def _validate_inputs(
    *,
    selection_ids_path: Path,
    qualification_report_path: Path,
    qualification_output_hashes_path: Path,
    qualified_task_ids_path: Path,
    failed_task_ids_path: Path,
    correction_manifest_path: Path,
    correction_root: Path,
    inventory_path: Path,
    split_path: Path,
    source_root: Path,
) -> dict[str, Any]:
    selected_ids = _read_ids(selection_ids_path, "selection IDs")
    if not selected_ids:
        raise QualifiedSubsetError("selection IDs are empty")
    selection_ids_hash = sha256_file(selection_ids_path)
    qualified_task_ids = _read_ids(qualified_task_ids_path, "qualified task IDs")
    failed_task_ids = _read_optional_ids(failed_task_ids_path, "failed task IDs")
    hashes = _normalize_qualification_output_hashes(
        _load_json(qualification_output_hashes_path)
    )
    qualified_ids_hash = sha256_file(qualified_task_ids_path)
    failed_ids_hash = sha256_file(failed_task_ids_path)
    report_hash = sha256_file(qualification_report_path)
    correction_hash = sha256_file(correction_manifest_path)
    if hashes.get("qualified_task_ids_sha256") != qualified_ids_hash:
        raise QualifiedSubsetError("qualified task list hash is not bound")
    if hashes.get("failed_or_inconclusive_task_ids_sha256") != failed_ids_hash:
        raise QualifiedSubsetError("failed task list hash is not bound")
    if hashes.get("qualification_validation_report_sha256") != report_hash:
        raise QualifiedSubsetError("qualification report hash is not bound")
    if hashes.get("selection_ids_sha256") not in {None, selection_ids_hash}:
        raise QualifiedSubsetError("selection ID hash is not bound")
    if sha256_file(inventory_path) != BASE_INVENTORY_SHA256:
        raise QualifiedSubsetError("inventory hash mismatch")
    if sha256_file(split_path) != BASE_SPLIT_SHA256:
        raise QualifiedSubsetError("split hash mismatch")
    actual_source_tree, symlink_count, tree_errors = source_tree_sha256(source_root)
    if tree_errors or symlink_count:
        raise QualifiedSubsetError("source tree contains invalid entries")
    if actual_source_tree != SOURCE_TREE_SHA256:
        raise QualifiedSubsetError("source tree hash mismatch")
    report = _load_json(qualification_report_path)
    if report.get("qualification_evidence_sha256") != hashes["qualification_evidence_sha256"]:
        raise QualifiedSubsetError("qualification evidence hash is not bound")
    if report.get("qualification_sidecar_sha256") != hashes["runner_sidecar_sha256"]:
        raise QualifiedSubsetError("qualification runner sidecar hash is not bound")
    if report.get("correction_manifest_sha256") != correction_hash:
        raise QualifiedSubsetError("qualification report correction manifest hash mismatch")
    if hashes.get("asset_manifest_sha256") not in {None, correction_hash}:
        raise QualifiedSubsetError("qualification output asset manifest hash mismatch")
    qualified_rows, failed_rows = _qualification_rows(
        qualification_report=report,
        selected_ids=selected_ids,
        qualified_task_ids=qualified_task_ids,
        failed_task_ids=failed_task_ids,
    )
    inventory = {row.get("source_id"): row for row in _load_jsonl(inventory_path)}
    split = _load_json(split_path)
    train_ids = set(split.get("splits", {}).get("train", [])) if isinstance(split, dict) else set()
    corrections = _load_jsonl(correction_manifest_path)
    correction_by_source = {row.get("source_id"): row for row in corrections}
    if len(corrections) != len(selected_ids) or [row.get("source_id") for row in corrections] != selected_ids:
        raise QualifiedSubsetError("correction manifest does not match the pinned selection order")
    correction_versions = {row.get("correction_version") for row in corrections}
    if len(correction_versions) != 1 or not isinstance(next(iter(correction_versions), None), str):
        raise QualifiedSubsetError("correction manifest has inconsistent correction versions")
    correction_version = next(iter(correction_versions))
    for source_id in selected_ids:
        source = inventory.get(source_id)
        correction = correction_by_source.get(source_id)
        if source is None or correction is None:
            raise QualifiedSubsetError(f"missing inventory or correction row: {source_id}")
        if source.get("source_commit") != SOURCE_COMMIT:
            raise QualifiedSubsetError(f"source commit mismatch: {source_id}")
        if correction.get("upstream_commit") != SOURCE_COMMIT:
            raise QualifiedSubsetError(f"correction source commit mismatch: {source_id}")
        if source_id not in train_ids or correction.get("split") != "train":
            raise QualifiedSubsetError(f"qualified source is not train-only: {source_id}")
        if correction.get("correction_version") != correction_version:
            raise QualifiedSubsetError(f"correction version mismatch: {source_id}")
        if correction.get("dependency_closure") != "passed" or correction.get("verification_readiness") != "pending_qualification":
            raise QualifiedSubsetError(f"correction is not the immutable pending qualification row: {source_id}")
        if correction.get("qualification_status") != "pending_isolated_qualification":
            raise QualifiedSubsetError(f"correction qualification state was unexpectedly changed: {source_id}")
        if correction.get("support_files") != [] or correction.get("reference_modified") is not False or correction.get("reference_copied_to_support") is not False:
            raise QualifiedSubsetError(f"correction privacy contract failed: {source_id}")
        if correction.get("task_id") != source.get("task_id"):
            raise QualifiedSubsetError(f"correction task identity mismatch: {source_id}")
        testbench = _safe_testbench(correction_root, correction)
        if sha256_file(testbench) != correction.get("corrected_testbench_sha256"):
            raise QualifiedSubsetError(f"corrected testbench hash mismatch: {source_id}")
    return {
        "selected_ids": selected_ids,
        "qualified_task_ids": qualified_task_ids,
        "failed_task_ids": failed_task_ids,
        "qualified_rows": qualified_rows,
        "failed_rows": failed_rows,
        "report": report,
        "hashes": hashes,
        "inventory": inventory,
        "corrections": corrections,
        "correction_by_source": correction_by_source,
        "correction_version": correction_version,
        "selection_ids_sha256": selection_ids_hash,
        "correction_manifest_sha256": correction_hash,
    }


def prepare_qualified_normalization_run(
    *,
    source_input: Path,
    source_root: Path,
    inventory_path: Path,
    split_path: Path,
    selection_ids_path: Path,
    qualification_report_path: Path,
    qualification_output_hashes_path: Path,
    qualified_task_ids_path: Path,
    failed_task_ids_path: Path,
    correction_manifest_path: Path,
    correction_root: Path,
    run_root: Path,
) -> tuple[dict[str, Any], int]:
    try:
        if run_root.is_symlink() or not run_root.is_dir():
            raise QualifiedSubsetError("run root must be an initialized regular directory")
        if not (run_root / "run_manifest.json").is_file():
            raise QualifiedSubsetError("qualified generation run requires run_manifest.json")
        values = _validate_inputs(
            selection_ids_path=selection_ids_path,
            qualification_report_path=qualification_report_path,
            qualification_output_hashes_path=qualification_output_hashes_path,
            qualified_task_ids_path=qualified_task_ids_path,
            failed_task_ids_path=failed_task_ids_path,
            correction_manifest_path=correction_manifest_path,
            correction_root=correction_root,
            inventory_path=inventory_path,
            split_path=split_path,
            source_root=source_root,
        )
        _validate_source_input_alignment(
            source_input=source_input,
            source_root=source_root,
            inventory=values["inventory"],
            selected_ids=values["selected_ids"],
        )
        reports_root = run_root / "reports"
        qualified_source_ids_path = reports_root / "qualified_source_ids.txt"
        failed_source_ids_path = reports_root / "failed_source_ids.txt"
        subset_manifest_path = reports_root / "qualified_correction_manifest.jsonl"
        binding_path = reports_root / "qualified_subset_binding.json"
        attestation_path = reports_root / "qualified_normalization_preparation.json"
        qualified_source_ids = [row["source_id"] for row in values["qualified_rows"]]
        failed_source_ids = [row["source_id"] for row in values["failed_rows"]]
        _write_exclusive(qualified_source_ids_path, ("\n".join(qualified_source_ids) + "\n").encode("utf-8"))
        _write_exclusive(failed_source_ids_path, ("\n".join(failed_source_ids) + "\n").encode("utf-8"))
        subset_rows = []
        for source_id in qualified_source_ids:
            subset_row = dict(values["correction_by_source"][source_id])
            subset_row["qualification_status"] = "qualified"
            subset_row["verification_readiness"] = "executable_ready"
            subset_rows.append(subset_row)
        _write_jsonl(subset_manifest_path, subset_rows)
        binding = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_root.name,
            "qualification_scope": "qualification_only",
            "generic_manual_run_validator_applicable": False,
            "qualification_validator_authoritative": True,
            "source_commit": SOURCE_COMMIT,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "frozen_split_sha256": BASE_SPLIT_SHA256,
            "selection_ids_sha256": values["selection_ids_sha256"],
            "correction_version": values["correction_version"],
            "correction_manifest_sha256": values["correction_manifest_sha256"],
            "qualification_report_sha256": values["hashes"]["qualification_validation_report_sha256"],
            "qualification_evidence_sha256": values["report"].get("qualification_evidence_sha256"),
            "raw_runner_evidence_sha256": values["hashes"]["evidence_sha256"],
            "runner_sidecar_sha256": values["hashes"]["runner_sidecar_sha256"],
            "qualified_task_ids_sha256": sha256_file(qualified_task_ids_path),
            "failed_task_ids_sha256": sha256_file(failed_task_ids_path),
            "qualified_source_ids_sha256": sha256_file(qualified_source_ids_path),
            "failed_source_ids_sha256": sha256_file(failed_source_ids_path),
            "qualified_correction_manifest_sha256": sha256_file(subset_manifest_path),
            "qualified_task_count": len(values["qualified_task_ids"]),
            "failed_task_count": len(values["failed_task_ids"]),
            "normalization_allowed": True,
            "teacher_generation_allowed": False,
            "reference_rtl_supplied": False,
            "support_file_count": 0,
            "rows": [
                {
                    "source_id": row["source_id"],
                    "task_id": row["task_id"],
                    "qualification_result": "passed",
                    "qualification_status": "qualified",
                    "corrected_testbench_sha256": values["correction_by_source"][row["source_id"]]["corrected_testbench_sha256"],
                    "qualification_report_sha256": values["hashes"]["qualification_validation_report_sha256"],
                    "raw_runner_evidence_sha256": values["hashes"]["evidence_sha256"],
                    "runner_sidecar_sha256": values["hashes"]["runner_sidecar_sha256"],
                    "reference_supplied": False,
                    "support_files": [],
                }
                for row in values["qualified_rows"]
            ],
        }
        _write_json(binding_path, binding)
        export_result, export_code = export_generation_normalization_batches(
            source_input,
            run_root / "normalization" / "packets",
            run_root / "private_assets",
            batch_size=len(qualified_source_ids),
            source_commit=SOURCE_COMMIT,
            source_ids=qualified_source_ids,
            correction_manifest=subset_manifest_path,
            correction_root=correction_root,
            correction_version=values["correction_version"],
        )
        if export_code:
            return {"ok": False, "stage": "export", "binding": binding, "export": export_result}, 1
        batch_paths = sorted((run_root / "normalization" / "packets").glob("batch_*.json"))
        if len(batch_paths) != 1:
            raise QualifiedSubsetError("qualified normalization export must contain exactly one batch")
        batch = _load_json(batch_paths[0])
        rows = batch.get("rows") if isinstance(batch, dict) else None
        if not isinstance(rows, list) or len(rows) != len(qualified_source_ids):
            raise QualifiedSubsetError("qualified normalization batch row count does not match the qualified subset")
        if [row.get("source_id") for row in rows] != qualified_source_ids:
            raise QualifiedSubsetError("qualified normalization batch order mismatch")
        public_text = batch_paths[0].read_text(encoding="utf-8").casefold()
        for marker in ("refmodule", "reference.sv", "testbench.sv", ".local_data", "/home/", "/tmp/", "private_assets"):
            if marker in public_text:
                raise QualifiedSubsetError(f"public normalization batch contains forbidden marker: {marker}")
        assets_path = run_root / "private_assets" / "verification_assets.jsonl"
        if not assets_path.is_file():
            raise QualifiedSubsetError("qualified normalization export did not create private assets")
        attestation = {
            "schema_version": PREPARATION_SCHEMA_VERSION,
            "run_id": run_root.name,
            "qualification_binding_sha256": sha256_file(binding_path),
            "qualified_correction_manifest_sha256": sha256_file(subset_manifest_path),
            "batch_sha256": sha256_file(batch_paths[0]),
            "batch_row_count": len(rows),
            "qualified_source_ids": qualified_source_ids,
            "failed_source_ids": failed_source_ids,
            "private_assets_sha256": sha256_file(assets_path),
            "public_reference_exposed": False,
            "public_testbench_exposed": False,
            "errors": [],
        }
        _write_json(attestation_path, attestation)
        return {
            "ok": True,
            "stage": "export",
            "binding": binding,
            "export": export_result,
            "attestation": attestation,
        }, 0
    except (QualifiedSubsetError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "stage": "qualified_normalization", "errors": [str(exc)]}, 1


__all__ = [
    "CORRECTION_VERSION",
    "PREPARATION_SCHEMA_VERSION",
    "QUALIFICATION_REPORT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "QualifiedSubsetError",
    "prepare_qualified_normalization_run",
    "sha256_file",
]
