"""Prepare and validate the bounded batch-20 asset qualification gate.

This module is deliberately a control-plane boundary.  Preparation reads the
public task metadata and the versioned corrected testbench overlay, then
stages independently authored positive and negative candidate fixtures for
the existing isolated ``pilot-docker`` candidate runner.  It never reads
reference RTL, executes HDL, calls a model, or changes the correction overlay.

The pinned RTLBench launcher accepts candidate manifests rather than mutation
manifests.  Qualification therefore represents each positive/negative case as
one candidate row and aggregates the runner evidence back into per-asset
qualification results.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any, Iterable

from scripts.dataset.rtl_generation_batch_selection import (
    BATCH20_SOURCE_IDS,
    BASE_INVENTORY_SHA256,
    BASE_SPLIT_SHA256,
    CORRECTION_VERSION,
    SELECTION_SCHEMA_VERSION,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
    sha256_file,
)
from scripts.dataset.rtl_generation_batch_corrections import (
    is_qualification_retry_selection,
)


QUALIFICATION_CASE_SCHEMA_VERSION = "rtl_asset_qualification_case_v0.1"
AUTHORING_ROW_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"
AUTHORIZATION_SCHEMA_VERSION = "rtl_asset_qualification_execution_authorization_v0.1"
REPORT_SCHEMA_VERSION = "rtl_asset_qualification_report_v0.1"
EVIDENCE_SCHEMA_VERSION = "rtl_asset_qualification_case_evidence_v0.1"
RUNNER_SCHEMA_VERSION = "rtlbench_runner_identity_v0.2"
RUN_ID = "pilot_004_assetfix_v003"
IMAGE_ID = "sha256:004331efd280c2c94a7a25d920f9e008c0f552237d0d66902806a327033ead9b"
RTLBench_COMMIT = "fcad47eb03e469097432229e1285b9239fd23a00"
RTLSPECIALIZER_BASE_COMMIT = "c34c0bdea01b4460bbe49325e3ea530e4778d2b0"

PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
    "refmodule",
    "testbench",
    "candidate_evidence",
    "mutation_evidence",
    "simulator",
    "iverilog",
    "verilator",
    "yosys",
)
FORBIDDEN_RTL_MARKERS = ("`include", "package ", "interface ", "bind ")
MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

SMOKE_REVIEW_EXCEPTIONS = [
    {
        "code": "normalized_metadata_mismatch",
        "source_id": source_id,
        "disposition": "human_accepted_for_experimental_training",
    }
    for source_id in (
        "Prob020_mt2015_eq2",
        "Prob071_always_casez",
        "Prob048_m2014_q4c",
        "Prob079_fsm3onehot",
    )
]


class QualificationError(ValueError):
    """Raised when qualification preparation or validation fails closed."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _load_json(path: Path) -> Any:
    _require_regular(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"could not load JSON {path.name}: {exc}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    _require_regular(path)
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise QualificationError(f"could not load JSONL {path.name}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QualificationError(f"{path.name}:{line_number}: malformed JSON") from exc
        if not isinstance(value, dict):
            raise QualificationError(f"{path.name}:{line_number}: row is not an object")
        rows.append(value)
    return rows


def _require_regular(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise QualificationError(f"required path is unavailable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise QualificationError(f"required path is not a regular file: {path}")


def _require_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise QualificationError(f"required directory is unavailable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise QualificationError(f"required path is not a directory: {path}")


def _require_empty_staged_output(qualification_root: Path) -> Path:
    """Require the runner's output parent before authorization or execution."""
    staged = qualification_root / "staged"
    _require_directory(staged)
    metadata = staged.lstat()
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise QualificationError(f"runner output directory is not 0700: {staged}")
    if any(staged.iterdir()):
        raise QualificationError(f"runner output directory is not empty: {staged}")
    return staged


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise QualificationError("artifact paths must be relative POSIX paths")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise QualificationError(f"unsafe artifact path: {value}")
    return value


def _copy_regular(source: Path, destination: Path) -> None:
    _require_regular(source)
    source_stat = source.lstat()
    if source_stat.st_nlink != 1:
        raise QualificationError(f"source is hard-linked: {source}")
    if destination.exists() or destination.is_symlink():
        raise QualificationError(f"refusing to replace staged file: {destination}")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)


def _contained_path(root: Path, relative: str, *, label: str) -> Path:
    """Resolve an artifact path and require it to remain below its root."""
    root_resolved = root.resolve()
    path = root / relative
    try:
        path.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise QualificationError(f"{label} escapes its declared root") from exc
    return path


def _normalize_qualification_correction(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize compact assetfix_v010 rows for the qualification preparer.

    The v010 static-authoring manifest intentionally stores fixture hashes as
    a compact mapping.  The older qualification-preparation contract expects
    explicit mutation contracts and a workspace-relative testbench path.  Do
    this in memory so the immutable correction manifest remains byte-for-byte
    unchanged.
    """
    if row.get("mutation_contracts") is not None:
        return row
    if row.get("correction_version") != "assetfix_v010":
        return row

    source_id = row.get("source_id")
    fixture_hashes = row.get("fixture_hashes")
    if not isinstance(source_id, str) or not isinstance(fixture_hashes, dict):
        raise QualificationError(
            f"assetfix_v010 row lacks fixture hashes: {source_id}"
        )
    fixture_names = list(fixture_hashes)
    if "positive" not in fixture_names:
        raise QualificationError(
            f"assetfix_v010 row lacks positive fixture: {source_id}"
        )
    negative_names = sorted(name for name in fixture_names if name != "positive")
    if not negative_names:
        raise QualificationError(
            f"assetfix_v010 row lacks negative fixtures: {source_id}"
        )

    normalized = dict(row)
    normalized["testbench_path"] = f"tasks/{source_id}/testbench.sv"
    normalized["public_specification_sha256"] = row.get(
        "original_prompt_sha256"
    )
    normalized["mutation_contracts"] = [
        {
            "execution_status": "pending_isolated_qualification",
            "expected_outcome": "accepted",
            "kind": "positive",
            "name": "public_spec_candidate",
            "oracle_basis": "public_specification_only",
            "schema_version": "rtl_correction_mutation_contract_v0.1",
        },
        *(
            {
                "execution_status": "pending_isolated_qualification",
                "expected_outcome": "rejected",
                "kind": "negative",
                "name": name,
                "oracle_basis": "public_specification_only",
                "schema_version": "rtl_correction_mutation_contract_v0.1",
            }
            for name in negative_names
        ),
    ]
    return normalized


def _write_exclusive(path: Path, value: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise QualificationError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value)
    os.chmod(path, mode)


def _write_json(path: Path, value: Any) -> None:
    _write_exclusive(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    _write_exclusive(path, b"".join(_json_bytes(row) for row in rows))


def _workspace_tree_sha256(root: Path) -> str:
    _require_directory(root)
    records: list[bytes] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise QualificationError(f"workspace contains a symlink: {path}")
            if not stat.S_ISDIR(metadata.st_mode):
                raise QualificationError(f"workspace contains a special file: {path}")
            records.append(b"D\0" + path.relative_to(root).as_posix().encode() + b"\n")
        for name in files:
            path = Path(current) / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise QualificationError(f"workspace contains an invalid file: {path}")
            records.append(b"F\0" + path.relative_to(root).as_posix().encode() + b"\0" + sha256_file(path).encode() + b"\n")
    return hashlib.sha256(b"".join(records)).hexdigest()


def _validate_private_tree_permissions(root: Path) -> tuple[int, int, int]:
    """Require the generated qualification tree's restrictive mode contract."""
    _require_directory(root)
    root_metadata = root.lstat()
    if stat.S_IMODE(root_metadata.st_mode) != 0o700:
        raise QualificationError(f"generated directory is not 0700: {root}")
    uid = root_metadata.st_uid
    gid = root_metadata.st_gid
    count = 0
    for path in (root, *sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())):
        metadata = path.lstat()
        if metadata.st_uid != uid or metadata.st_gid != gid:
            raise QualificationError(f"generated tree owner mismatch: {path}")
        if stat.S_ISLNK(metadata.st_mode):
            raise QualificationError(f"generated tree contains a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise QualificationError(f"generated directory is not 0700: {path}")
        elif stat.S_ISREG(metadata.st_mode):
            if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
                raise QualificationError(f"generated file is not a private 0600 regular file: {path}")
            count += 1
        else:
            raise QualificationError(f"generated tree contains a special file: {path}")
    return uid, gid, count


def _public_rtl_errors(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"candidate is not valid UTF-8: {exc}"]
    lowered = text.casefold()
    errors = [f"private marker in candidate: {marker}" for marker in PRIVATE_MARKERS if marker.casefold() in lowered]
    errors.extend(f"forbidden dependency in candidate: {marker}" for marker in FORBIDDEN_RTL_MARKERS if marker.casefold() in lowered)
    modules = MODULE_RE.findall(text)
    if modules != ["TopModule"]:
        errors.append("candidate must declare exactly one TopModule and no helper module")
    return sorted(set(errors))


def _selection_and_manifest(
    *,
    selection_path: Path,
    ids_path: Path,
    correction_manifest_path: Path,
    inventory_path: Path,
    split_path: Path,
    expected_correction_manifest_sha256: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selection = _load_json(selection_path)
    ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    corrections = [
        _normalize_qualification_correction(row)
        for row in _load_jsonl(correction_manifest_path)
    ]
    inventory = _load_jsonl(inventory_path)
    split = _load_json(split_path)
    if not ids or len(ids) != len(set(ids)):
        raise QualificationError("selection IDs must be a non-empty unique ordered list")
    if sha256_file(ids_path) != selection.get("selection_ids_sha256"):
        raise QualificationError("selection ID hash mismatch")
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise QualificationError("selection schema mismatch")
    selection_rows = selection.get("rows")
    if selection_rows is None:
        if ids != list(BATCH20_SOURCE_IDS):
            raise QualificationError("selection report rows are required for a custom batch")
    elif not isinstance(selection_rows, list) or [row.get("source_id") for row in selection_rows if isinstance(row, dict)] != ids:
        raise QualificationError("selection report order or row count mismatch")
    if selection_rows is not None and (selection.get("selected_count") != len(ids) or selection.get("split") != "train"):
        raise QualificationError("selection report count or split binding mismatch")
    if selection.get("errors") not in ([], None) or selection.get("ok") is False:
        raise QualificationError("selection report is not successful")
    for key, expected in (
        ("source_commit", SOURCE_COMMIT),
        ("source_tree_sha256", SOURCE_TREE_SHA256),
        ("base_inventory_sha256", BASE_INVENTORY_SHA256),
        ("base_split_sha256", BASE_SPLIT_SHA256),
    ):
        if selection.get(key) != expected:
            raise QualificationError(f"selection binding mismatch: {key}")
    correction_version = selection.get("correction_version")
    if not isinstance(correction_version, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", correction_version):
        raise QualificationError("selection correction version is invalid")
    if (
        len(ids) != len(BATCH20_SOURCE_IDS)
        and not 20 <= len(ids) <= 40
        and not is_qualification_retry_selection(selection)
    ):
        raise QualificationError("selection is outside the bounded 20-40 task range")
    if sha256_file(inventory_path) != BASE_INVENTORY_SHA256:
        raise QualificationError("inventory hash mismatch")
    if sha256_file(split_path) != BASE_SPLIT_SHA256:
        raise QualificationError("frozen split hash mismatch")
    if len(corrections) != len(ids) or [row.get("source_id") for row in corrections] != ids:
        raise QualificationError("correction manifest order or row count mismatch")
    if len({row.get("source_id") for row in corrections}) != len(corrections):
        raise QualificationError("correction manifest contains duplicate source IDs")
    inventory_by_id = {row.get("source_id"): row for row in inventory}
    train_ids = set((split.get("splits") or {}).get("train", []))
    for source_id, correction in zip(ids, corrections):
        source = inventory_by_id.get(source_id)
        if source is None or source_id not in train_ids:
            raise QualificationError(f"selected source is not a pinned train row: {source_id}")
        required_binding = (
            ("public_specification_sha256", source.get("source_prompt_sha256")),
            ("selection_ids_sha256", sha256_file(ids_path)),
            ("selection_report_sha256", sha256_file(selection_path)),
        ) if correction_version == "assetfix_v004" else ()
        for key, expected in (
            ("task_id", source.get("task_id")),
            ("top_module", source.get("top_module")),
            ("split", "train"),
            ("source_id", source_id),
            ("correction_version", correction_version),
            ("upstream_commit", SOURCE_COMMIT),
            ("source_tree_sha256", SOURCE_TREE_SHA256),
            ("frozen_split_sha256", BASE_SPLIT_SHA256),
            ("qualification_status", "pending_isolated_qualification"),
            ("verification_readiness", "pending_qualification"),
            ("dependency_closure", "passed"),
            ("support_files", []),
            ("reference_modified", False),
            ("reference_copied_to_support", False),
            *required_binding,
        ):
            if correction.get(key) != expected:
                raise QualificationError(f"correction manifest binding mismatch: {source_id}:{key}")
        contracts = correction.get("mutation_contracts")
        if not isinstance(contracts, list) or not any(item.get("kind") == "positive" for item in contracts if isinstance(item, dict)):
            raise QualificationError(f"positive qualification contract missing: {source_id}")
        if not any(item.get("kind") == "negative" for item in contracts if isinstance(item, dict)):
            raise QualificationError(f"negative qualification contract missing: {source_id}")
    correction_hash = sha256_file(correction_manifest_path)
    if expected_correction_manifest_sha256 is not None and correction_hash != expected_correction_manifest_sha256:
        raise QualificationError("correction manifest hash mismatch")
    return selection, corrections, inventory, {
        "source_ids": ids,
        "correction_version": correction_version,
        "ids_sha256": sha256_file(ids_path),
        "selection_sha256": sha256_file(selection_path),
        "correction_manifest_sha256": correction_hash,
        "inventory_sha256": sha256_file(inventory_path),
        "split_sha256": sha256_file(split_path),
    }


def _authoring_rows(
    path: Path,
    corrections: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = _load_jsonl(path)
    source_ids = source_ids if source_ids is not None else list(BATCH20_SOURCE_IDS)
    if [row.get("source_id") for row in rows] != source_ids:
        raise QualificationError("authoring rows do not preserve pinned task order")
    inventory_by_id = {row.get("source_id"): row for row in inventory}
    correction_by_id = {row.get("source_id"): row for row in corrections}
    for row in rows:
        required = {"schema_version", "source_id", "task_id", "top_module", "positive_rtl_path", "negative_mutations", "reference_used", "support_files"}
        optional_metadata = {
            "lineage",
            "prior_assetfix_versions",
            "selection_role",
        }
        if not required.issubset(row) or set(row) - required - optional_metadata:
            raise QualificationError(f"authoring row has an invalid field set: {row.get('source_id')}")
        source_id = row["source_id"]
        source = inventory_by_id[source_id]
        if row["schema_version"] != AUTHORING_ROW_SCHEMA_VERSION or row["task_id"] != source.get("task_id") or row["top_module"] != source.get("top_module"):
            raise QualificationError(f"authoring identity mismatch: {source_id}")
        if row["reference_used"] is not False or row["support_files"] != []:
            raise QualificationError(f"authoring row supplied reference/support content: {source_id}")
        _safe_relative(row["positive_rtl_path"])
        negatives = row["negative_mutations"]
        if not isinstance(negatives, list) or not negatives:
            raise QualificationError(f"authoring negatives are missing: {source_id}")
        expected_names = [item.get("name") for item in correction_by_id[source_id]["mutation_contracts"] if item.get("kind") == "negative"]
        actual_names = [item.get("name") for item in negatives]
        if actual_names != expected_names or len(actual_names) != len(set(actual_names)):
            raise QualificationError(f"authoring mutation order mismatch: {source_id}")
        for item in negatives:
            if set(item) != {"name", "rtl_path", "authoring_method", "oracle_basis", "expected_outcome"}:
                raise QualificationError(f"authoring mutation fields invalid: {source_id}")
            if item["expected_outcome"] != "rejected" or item["oracle_basis"] != "public_specification_only":
                raise QualificationError(f"authoring mutation oracle is invalid: {source_id}:{item['name']}")
            _safe_relative(item["rtl_path"])
    return rows


def prepare_qualification_input(
    *,
    selection_path: Path,
    ids_path: Path,
    correction_manifest_path: Path,
    correction_root: Path,
    inventory_path: Path,
    split_path: Path,
    authoring_manifest_path: Path,
    authoring_root: Path,
    output_root: Path,
    expected_correction_manifest_sha256: str | None = None,
    run_id: str = RUN_ID,
) -> dict[str, Any]:
    if output_root.exists() or output_root.is_symlink():
        raise QualificationError("qualification output already exists")
    _require_directory(correction_root)
    _require_directory(authoring_root)
    selection, corrections, inventory, hashes = _selection_and_manifest(
        selection_path=selection_path,
        ids_path=ids_path,
        correction_manifest_path=correction_manifest_path,
        inventory_path=inventory_path,
        split_path=split_path,
        expected_correction_manifest_sha256=expected_correction_manifest_sha256,
    )
    source_ids = hashes["source_ids"]
    correction_version = hashes["correction_version"]
    authors = _authoring_rows(authoring_manifest_path, corrections, inventory, source_ids)
    author_by_id = {row["source_id"]: row for row in authors}
    output_root.mkdir(mode=0o700, parents=True)
    staged_output = output_root / "staged"
    staged_output.mkdir(mode=0o700)
    os.chmod(staged_output, 0o700)
    input_root = output_root / "input"
    input_root.mkdir(mode=0o700)
    workspace = input_root / "workspace"
    workspace.mkdir(mode=0o700)

    correction_by_id = {row["source_id"]: row for row in corrections}
    inventory_by_id = {row["source_id"]: row for row in inventory}
    case_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    case_number = 0
    for source_id in source_ids:
        source = inventory_by_id[source_id]
        correction = correction_by_id[source_id]
        author = author_by_id[source_id]
        task_dir = workspace / source_id
        task_dir.mkdir(mode=0o700)
        testbench_relative = correction["testbench_path"]
        testbench_source = _contained_path(
            correction_root,
            testbench_relative,
            label=f"corrected testbench for {source_id}",
        )
        testbench_target = task_dir / "testbench.sv"
        _copy_regular(testbench_source, testbench_target)
        if sha256_file(testbench_source) != correction.get("corrected_testbench_sha256"):
            raise QualificationError(f"corrected testbench hash mismatch: {source_id}")
        # The corrected testbench is audited by the static correction gate; this
        # check only prevents private-path content from entering the staged
        # workspace during preparation.
        if any(marker.casefold() in testbench_target.read_text(encoding="utf-8").casefold() for marker in PRIVATE_MARKERS if marker not in {"testbench", "candidate_evidence", "mutation_evidence"}):
            raise QualificationError(f"private marker in corrected testbench: {source_id}")
        negative_items = author["negative_mutations"]
        cases = [("positive", "public_spec_candidate", author["positive_rtl_path"], "accepted", "trusted_manual_public_spec")]
        cases.extend(("negative", item["name"], item["rtl_path"], item["expected_outcome"], item["authoring_method"]) for item in negative_items)
        for kind, name, source_relative, expected_outcome, authoring_method in cases:
            source_candidate = _contained_path(
                authoring_root,
                source_relative,
                label=f"qualification candidate for {source_id}",
            )
            candidate_target = task_dir / f"{name}.sv"
            _copy_regular(source_candidate, candidate_target)
            errors = _public_rtl_errors(candidate_target)
            if errors:
                raise QualificationError(f"invalid qualification candidate {source_id}:{name}: {'; '.join(errors)}")
            case_number += 1
            # The attempt number is local to the source ID and keeps the
            # existing candidate-manifest uniqueness rule satisfied.
            attempt = 1 if kind == "positive" else 1 + len([row for row in case_rows if row["source_id"] == source_id])
            candidate_id = f"{correction_version}__{source_id}__{name}"
            candidate_relative = f"{source_id}/{name}.sv"
            testbench_relative_workspace = f"{source_id}/testbench.sv"
            candidate_hash = sha256_file(candidate_target)
            testbench_hash = sha256_file(testbench_target)
            case_id = f"qualification_{case_number:03d}_{source_id}_{name}"
            case_rows.append({
                "schema_version": QUALIFICATION_CASE_SCHEMA_VERSION,
                "qualification_case_id": case_id,
                "source_id": source_id,
                "task_id": source["task_id"],
                "top_module": source["top_module"],
                "candidate_id": candidate_id,
                "attempt": attempt,
                "kind": kind,
                "mutation_name": name,
                "expected_outcome": expected_outcome,
                "authoring_method": authoring_method,
                "candidate_rtl_path": candidate_relative,
                "testbench_path": testbench_relative_workspace,
                "support_files": [],
                "candidate_sha256": candidate_hash,
                "testbench_sha256": testbench_hash,
                "reference_rtl_supplied": False,
            })
            candidate_rows.append({
                "schema_version": "rtl_candidate_manifest_v0.1",
                "candidate_id": candidate_id,
                "task_id": source["task_id"],
                "source_id": source_id,
                "attempt": attempt,
                "top_module": source["top_module"],
                "testbench_top": "tb",
                "candidate_rtl_path": candidate_relative,
                "testbench_path": testbench_relative_workspace,
                "support_files": [],
                "simulation_result_contract": "mismatch_count_v1",
                "requested_checks": {"compile": True, "simulation": True, "lint": False, "synthesis": False},
            })
    if len(case_rows) != len(candidate_rows):
        raise QualificationError("qualification case and candidate manifests differ in row count")
    _write_jsonl(output_root / "case_manifest.jsonl", case_rows)
    _write_jsonl(input_root / "candidate_manifest.jsonl", candidate_rows)
    manifest_hash = sha256_file(input_root / "candidate_manifest.jsonl")
    workspace_hash = _workspace_tree_sha256(workspace)
    report = {
        "schema_version": "rtl_asset_qualification_preparation_v0.1",
        "run_id": run_id,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": hashes["ids_sha256"],
        "selection_report_sha256": hashes["selection_sha256"],
        "correction_manifest_sha256": hashes["correction_manifest_sha256"],
        "inventory_sha256": hashes["inventory_sha256"],
        "split_sha256": hashes["split_sha256"],
        "correction_version": correction_version,
        "selected_source_ids": source_ids,
        "case_count": len(case_rows),
        "positive_case_count": sum(row["kind"] == "positive" for row in case_rows),
        "negative_case_count": sum(row["kind"] == "negative" for row in case_rows),
        "candidate_manifest_sha256": manifest_hash,
        "workspace_tree_sha256": workspace_hash,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_status_before": "pending_isolated_qualification",
        "teacher_generation_allowed": False,
        "errors": [],
    }
    _write_json(output_root / "preparation_report.json", report)
    return report


def _status(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _candidate_outcome(case: dict[str, Any], evidence: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
    compile_status = _status((_status(checks.get("compile"))).get("candidate"))
    simulation_status = _status((_status(checks.get("simulation"))).get("candidate_passes"))
    mismatch = evidence.get("mismatch_summary") if isinstance(evidence.get("mismatch_summary"), dict) else {}
    counts = mismatch.get("reported_counts") if isinstance(mismatch.get("reported_counts"), list) else []
    maximum = mismatch.get("maximum_count")
    timeout = mismatch.get("timeout_reported") is True or compile_status.get("reason") == "timeout" or simulation_status.get("reason") == "timeout"
    if timeout:
        return "timeout", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": True}
    if compile_status.get("passed") is not True:
        return "compile_failure", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": False}
    if simulation_status.get("attempted") is not True or not counts:
        return "simulation_result_missing", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": False}
    if case["kind"] == "positive":
        if evidence.get("accepted") is True and simulation_status.get("passed") is True and maximum == 0:
            return "passed", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": False}
        return "positive_candidate_failed", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": False}
    if evidence.get("accepted") is False and maximum is not None and maximum > 0:
        return "mutation_detected", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": False}
    return "mutation_not_detected", {"compile": compile_status, "simulation": simulation_status, "reported_counts": counts, "maximum_count": maximum, "timeout_reported": False}


def _validate_sidecar(sidecar: dict[str, Any], *, image_id: str, rtlbench_commit: str, manifest_hash: str, workspace_hash: str, evidence_hash: str) -> list[str]:
    errors: list[str] = []
    expected = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "profile": "pilot-docker",
        "runtime": "docker",
        "runtime_mode": "rootful-daemon",
        "rootless": False,
        "image_id": image_id,
        "rtlbench_commit": rtlbench_commit,
        "network_policy": "none",
        "partial_evidence_sha256": None,
        "manifest_sha256": manifest_hash,
        "workspace_tree_sha256": workspace_hash,
        "evidence_sha256": evidence_hash,
    }
    for key, value in expected.items():
        if sidecar.get(key) != value:
            errors.append(f"runner sidecar mismatch: {key}")
    return errors


def _require_equal_files(before: Path | None, after: Path | None, label: str, errors: list[str]) -> None:
    if before is None and after is None:
        return
    if before is None or after is None:
        errors.append(f"{label} comparison is incomplete")
        return
    try:
        if before.read_bytes() != after.read_bytes():
            errors.append(f"{label} changed during execution")
    except OSError as exc:
        errors.append(f"{label} comparison failed: {exc}")


def aggregate_qualification_evidence(
    *,
    qualification_root: Path,
    evidence_path: Path,
    sidecar_path: Path,
    report_output: Path,
    evidence_output: Path,
    expected_image_id: str = IMAGE_ID,
    expected_rtlbench_commit: str = RTLBench_COMMIT,
    handoff_before_path: Path | None = None,
    handoff_after_path: Path | None = None,
    volumes_before_path: Path | None = None,
    volumes_after_path: Path | None = None,
    containers_before_path: Path | None = None,
    containers_after_path: Path | None = None,
) -> dict[str, Any]:
    case_rows = _load_jsonl(qualification_root / "case_manifest.jsonl")
    preparation_report = _load_json(qualification_root / "preparation_report.json")
    candidate_manifest = qualification_root / "input" / "candidate_manifest.jsonl"
    workspace = qualification_root / "input" / "workspace"
    evidence_rows = _load_jsonl(evidence_path)
    sidecar = _load_json(sidecar_path)
    if not isinstance(sidecar, dict):
        raise QualificationError("runner sidecar must be an object")
    selected_source_ids = preparation_report.get("selected_source_ids")
    if not isinstance(selected_source_ids, list) or not selected_source_ids or len(selected_source_ids) != len(set(selected_source_ids)):
        raise QualificationError("preparation report has an invalid selected source list")
    if len(case_rows) != len(evidence_rows) or not case_rows:
        raise QualificationError("qualification case and evidence row counts do not match")
    case_by_id = {row.get("candidate_id"): row for row in case_rows}
    evidence_by_id = {row.get("candidate_id"): row for row in evidence_rows}
    if len(case_by_id) != len(case_rows) or set(case_by_id) != set(evidence_by_id):
        raise QualificationError("qualification case/evidence candidate IDs do not match")
    if [row.get("candidate_id") for row in case_rows] != [row.get("candidate_id") for row in _load_jsonl(candidate_manifest)]:
        raise QualificationError("qualification case and candidate manifest order do not match")
    manifest_hash = sha256_file(candidate_manifest)
    workspace_hash = _workspace_tree_sha256(workspace)
    evidence_hash = sha256_file(evidence_path)
    errors = _validate_sidecar(sidecar, image_id=expected_image_id, rtlbench_commit=expected_rtlbench_commit, manifest_hash=manifest_hash, workspace_hash=workspace_hash, evidence_hash=evidence_hash)
    _require_equal_files(handoff_before_path, handoff_after_path, "qualification handoff", errors)
    _require_equal_files(volumes_before_path, volumes_after_path, "managed volume", errors)
    _require_equal_files(containers_before_path, containers_after_path, "managed container", errors)
    for path in qualification_root.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.name.casefold() == "reference.sv"):
            errors.append("qualification output contains a forbidden reference or link")
    derived_rows: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = {source_id: [] for source_id in selected_source_ids}
    for case in case_rows:
        evidence = evidence_by_id[case["candidate_id"]]
        if any(evidence.get(key) != case.get(key) for key in ("candidate_id", "task_id", "source_id", "attempt", "top_module")):
            errors.append(f"evidence identity mismatch: {case['candidate_id']}")
        hashes = evidence.get("input_hashes") if isinstance(evidence.get("input_hashes"), dict) else {}
        if hashes.get("candidate_rtl_sha256") != case["candidate_sha256"] or hashes.get("testbench_sha256") != case["testbench_sha256"] or hashes.get("support_files") != []:
            errors.append(f"evidence input hash mismatch: {case['candidate_id']}")
        diagnostics = evidence.get("diagnostics")
        if not isinstance(diagnostics, list) or any(
            isinstance(value, str) and any(
                marker in value.casefold()
                for marker in ("/home/", "/tmp/", "/root/", "reference.sv", "refmodule")
            )
            for value in diagnostics
        ):
            errors.append(f"evidence diagnostics are not sanitized: {case['candidate_id']}")
        candidate_path = workspace / case["candidate_rtl_path"]
        testbench_path = workspace / case["testbench_path"]
        try:
            if sha256_file(candidate_path) != case["candidate_sha256"]:
                errors.append(f"candidate workspace hash mismatch: {case['candidate_id']}")
            if sha256_file(testbench_path) != case["testbench_sha256"]:
                errors.append(f"testbench workspace hash mismatch: {case['candidate_id']}")
        except (OSError, QualificationError):
            errors.append(f"qualification workspace artifact missing: {case['candidate_id']}")
        outcome, details = _candidate_outcome(case, evidence)
        row = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "qualification_case_id": case["qualification_case_id"],
            "source_id": case["source_id"],
            "task_id": case["task_id"],
            "candidate_id": case["candidate_id"],
            "attempt": case["attempt"],
            "kind": case["kind"],
            "mutation_name": case["mutation_name"],
            "expected_outcome": case["expected_outcome"],
            "observed_outcome": outcome,
            "candidate_sha256": case["candidate_sha256"],
            "testbench_sha256": case["testbench_sha256"],
            "compile_passed": details["compile"].get("passed") is True,
            "simulation_attempted": details["simulation"].get("attempted") is True,
            "simulation_passed": details["simulation"].get("passed") is True,
            "reported_mismatch_counts": details["reported_counts"],
            "maximum_mismatch_count": details["maximum_count"],
            "timeout_reported": details["timeout_reported"],
            "failure_category": evidence.get("failure_category"),
        }
        derived_rows.append(row)
        if case["source_id"] not in by_source:
            errors.append(f"qualification case references an unselected source: {case['source_id']}")
        else:
            by_source[case["source_id"]].append(row)
    task_rows: list[dict[str, Any]] = []
    for source_id in selected_source_ids:
        rows = by_source[source_id]
        positive = [row for row in rows if row["kind"] == "positive"]
        negatives = [row for row in rows if row["kind"] == "negative"]
        qualified = (
            len(positive) == 1
            and positive[0]["observed_outcome"] == "passed"
            and bool(negatives)
            and all(row["observed_outcome"] == "mutation_detected" for row in negatives)
            and not errors
        )
        if qualified:
            status = "qualified"
        elif any(row["observed_outcome"] == "timeout" for row in rows):
            status = "timeout"
        elif any(row["observed_outcome"] == "simulation_result_missing" for row in rows):
            status = "simulation_result_missing"
        elif any(row["observed_outcome"] == "compile_failure" for row in rows):
            status = "compile_failure"
        elif positive and positive[0]["observed_outcome"] != "passed":
            status = "positive_candidate_failed"
        elif any(row["observed_outcome"] == "mutation_not_detected" for row in negatives):
            status = "mutation_not_detected"
        else:
            status = "inconclusive"
        task_rows.append({
            "source_id": source_id,
            "task_id": rows[0]["task_id"] if rows else None,
            "qualification_status": status,
            "qualification_passed": status == "qualified",
            "positive_candidate_passed": bool(positive and positive[0]["observed_outcome"] == "passed"),
            "negative_mutation_count": len(negatives),
            "negative_mutations_detected": sum(row["observed_outcome"] == "mutation_detected" for row in negatives),
        })
    if errors:
        task_rows = [{**row, "qualification_passed": False, "qualification_status": "runner_failure"} for row in task_rows]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": preparation_report.get("run_id", RUN_ID),
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "correction_version": preparation_report.get("correction_version"),
        "selection_ids_sha256": preparation_report.get("selection_ids_sha256"),
        "selection_report_sha256": preparation_report.get("selection_report_sha256"),
        "correction_manifest_sha256": preparation_report.get("correction_manifest_sha256"),
        "qualification_manifest_sha256": manifest_hash,
        "qualification_evidence_sha256": None,
        "qualification_sidecar_sha256": sha256_file(sidecar_path),
        "selected_tasks": len(selected_source_ids),
        "qualified_tasks": sum(row["qualification_passed"] for row in task_rows),
        "failed_qualification": sum(not row["qualification_passed"] for row in task_rows),
        "positive_candidates_passed": sum(row["positive_candidate_passed"] for row in task_rows),
        "negative_mutations_detected": sum(row["negative_mutations_detected"] for row in task_rows),
        "negative_mutations_total": sum(row["negative_mutation_count"] for row in task_rows),
        "timeouts": sum(row["qualification_status"] == "timeout" for row in task_rows),
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "runner": {key: sidecar.get(key) for key in ("schema_version", "profile", "runtime", "runtime_mode", "rootless", "image_id", "rtlbench_commit", "network_policy")},
        "rows": task_rows,
        "errors": sorted(set(errors)),
        "qualification_passed": not errors and all(row["qualification_passed"] for row in task_rows),
    }
    report["qualification_evidence_sha256"] = hashlib.sha256(b"".join(_json_bytes(row) for row in derived_rows)).hexdigest()
    _write_jsonl(evidence_output, derived_rows)
    _write_json(report_output, report)
    if report["qualification_passed"]:
        _write_exclusive(qualification_root / "qualified_task_ids.txt", ("\n".join(row["source_id"] for row in task_rows if row["qualification_passed"]) + "\n").encode("utf-8"))
    return report


def validate_prepared_qualification(
    *,
    selection_path: Path,
    ids_path: Path,
    correction_manifest_path: Path,
    correction_root: Path,
    inventory_path: Path,
    split_path: Path,
    authoring_manifest_path: Path,
    authoring_root: Path,
    qualification_root: Path,
    authorization_path: Path,
    report_output: Path,
    correction_static_report_path: Path | None = None,
    rtlbench_root: Path | None = None,
    rtlspecializer_root: Path | None = None,
    expected_image_id: str = IMAGE_ID,
    expected_rtlbench_commit: str = RTLBench_COMMIT,
    expected_rtlspecializer_commit: str = RTLSPECIALIZER_BASE_COMMIT,
) -> dict[str, Any]:
    """Validate the prepared input before any isolated execution.

    This is intentionally separate from evidence aggregation.  It verifies the
    exact staged boundary and authorization, but never invokes Docker,
    RTLBench, a compiler, a simulator, or any other executable HDL tool.
    """
    if report_output.exists() or report_output.is_symlink():
        raise QualificationError("refusing to replace existing preflight report")
    selection, corrections, inventory, hashes = _selection_and_manifest(
        selection_path=selection_path,
        ids_path=ids_path,
        correction_manifest_path=correction_manifest_path,
        inventory_path=inventory_path,
        split_path=split_path,
        expected_correction_manifest_sha256=None,
    )
    source_ids = hashes["source_ids"]
    correction_version = hashes["correction_version"]
    authors = _authoring_rows(authoring_manifest_path, corrections, inventory, source_ids)
    _require_directory(correction_root)
    _require_directory(authoring_root)
    _require_directory(qualification_root)
    _require_empty_staged_output(qualification_root)
    permission_uid, permission_gid, permission_file_count = _validate_private_tree_permissions(qualification_root)
    _validate_private_tree_permissions(authoring_root)

    correction_hash = sha256_file(correction_manifest_path)
    authoring_hash = sha256_file(authoring_manifest_path)
    static_hash: str | None = None
    if correction_static_report_path is not None:
        static = _load_json(correction_static_report_path)
        if not isinstance(static, dict) or static.get("ok") is not True or static.get("errors") != []:
            raise QualificationError("correction static-validation report is not successful")
        static_hash = sha256_file(correction_static_report_path)

    expected_top_level = {"case_manifest.jsonl", "input", "preparation_report.json", "staged"}
    actual_top_level = {path.name for path in qualification_root.iterdir()}
    unexpected_top_level = actual_top_level - expected_top_level - {"reports"}
    if unexpected_top_level:
        raise QualificationError("qualification attempt has unexpected top-level entries")
    reports_root = qualification_root / "reports"
    run_reports_root = qualification_root.parent.parent / "reports"
    if reports_root.exists():
        _require_directory(reports_root)
        report_entries = {path.name for path in reports_root.iterdir()}
        expected_report_name = authorization_path.name
        if authorization_path.parent == reports_root and report_entries != {expected_report_name}:
            raise QualificationError("qualification reports contain unexpected entries")
    if authorization_path.parent not in {reports_root, run_reports_root}:
        raise QualificationError("authorization must be in qualification reports or retry reports")
    if authorization_path.parent == run_reports_root:
        _require_directory(run_reports_root)
        report_entries = {path.name for path in run_reports_root.iterdir()}
        if report_entries != {authorization_path.name}:
            raise QualificationError("retry reports contain unexpected entries")
    authorization_metadata = authorization_path.lstat()
    if (
        stat.S_ISLNK(authorization_metadata.st_mode)
        or not stat.S_ISREG(authorization_metadata.st_mode)
        or stat.S_IMODE(authorization_metadata.st_mode) != 0o600
        or authorization_metadata.st_nlink != 1
        or authorization_metadata.st_uid != permission_uid
        or authorization_metadata.st_gid != permission_gid
    ):
        raise QualificationError("authorization is not a private 0600 regular file")
    input_root = qualification_root / "input"
    workspace = input_root / "workspace"
    _require_directory(input_root)
    _require_directory(workspace)
    if {path.name for path in input_root.iterdir()} != {"candidate_manifest.jsonl", "workspace"}:
        raise QualificationError("runner input contains unexpected staged entries")

    preparation = _load_json(qualification_root / "preparation_report.json")
    if not isinstance(preparation, dict):
        raise QualificationError("preparation report must be an object")
    expected_positive_count = len(source_ids)
    expected_negative_count = sum(
        sum(item.get("kind") == "negative" for item in correction.get("mutation_contracts", []))
        for correction in corrections
    )
    expected_case_count = expected_positive_count + expected_negative_count
    expected_preparation = {
        "schema_version": "rtl_asset_qualification_preparation_v0.1",
        "run_id": preparation.get("run_id", RUN_ID),
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": hashes["ids_sha256"],
        "selection_report_sha256": hashes["selection_sha256"],
        "correction_manifest_sha256": correction_hash,
        "inventory_sha256": hashes["inventory_sha256"],
        "split_sha256": hashes["split_sha256"],
        "correction_version": correction_version,
        "selected_source_ids": source_ids,
        "case_count": expected_case_count,
        "positive_case_count": expected_positive_count,
        "negative_case_count": expected_negative_count,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "qualification_status_before": "pending_isolated_qualification",
        "teacher_generation_allowed": False,
        "errors": [],
    }
    for key, expected in expected_preparation.items():
        if preparation.get(key) != expected:
            raise QualificationError(f"preparation report mismatch: {key}")

    case_rows = _load_jsonl(qualification_root / "case_manifest.jsonl")
    candidate_rows = _load_jsonl(input_root / "candidate_manifest.jsonl")
    correction_by_id = {row["source_id"]: row for row in corrections}
    if len(case_rows) != expected_case_count or len(candidate_rows) != expected_case_count:
        raise QualificationError("prepared qualification input case count mismatch")
    expected_case_sources = [
        source_id
        for source_id in source_ids
        for _ in range(1 + sum(item.get("kind") == "negative" for item in correction_by_id[source_id].get("mutation_contracts", [])))
    ]
    if [row.get("source_id") for row in case_rows] != expected_case_sources:
        raise QualificationError("case manifest does not preserve the pinned task order")
    if [row.get("source_id") for row in candidate_rows] != expected_case_sources:
        raise QualificationError("candidate manifest does not preserve the pinned task order")
    if len({row.get("candidate_id") for row in case_rows}) != expected_case_count:
        raise QualificationError("case manifest contains duplicate candidate IDs")
    if len({row.get("candidate_id") for row in candidate_rows}) != expected_case_count:
        raise QualificationError("candidate manifest contains duplicate candidate IDs")

    inventory_by_id = {row["source_id"]: row for row in inventory}
    author_by_id = {row["source_id"]: row for row in authors}
    candidate_by_id = {row["candidate_id"]: row for row in candidate_rows}
    case_by_id = {row["candidate_id"]: row for row in case_rows}
    for source_index, source_id in enumerate(source_ids):
        source = inventory_by_id[source_id]
        correction = correction_by_id[source_id]
        author = author_by_id[source_id]
        expected_names = [
            item["name"]
            for item in correction["mutation_contracts"]
            if item.get("kind") == "negative"
        ]
        expected_names = ["public_spec_candidate", *expected_names]
        expected_files = {"testbench.sv", *(f"{name}.sv" for name in expected_names)}
        task_dir = workspace / source_id
        _require_directory(task_dir)
        if {path.name for path in task_dir.iterdir()} != expected_files:
            raise QualificationError(f"staged workspace file set mismatch: {source_id}")
        testbench_path = task_dir / "testbench.sv"
        if sha256_file(testbench_path) != correction["corrected_testbench_sha256"]:
            raise QualificationError(f"staged corrected testbench hash mismatch: {source_id}")
        source_case_ids = [
            row["candidate_id"] for row in case_rows if row.get("source_id") == source_id
        ]
        expected_case_ids = [
            f"{correction_version}__{source_id}__{name}" for name in expected_names
        ]
        if source_case_ids != expected_case_ids:
            raise QualificationError(f"case order mismatch: {source_id}")
        for offset, name in enumerate(expected_names):
            candidate_id = f"{correction_version}__{source_id}__{name}"
            case = case_by_id[candidate_id]
            manifest = candidate_by_id[candidate_id]
            candidate_path = task_dir / f"{name}.sv"
            if case["task_id"] != source["task_id"] or case["top_module"] != source["top_module"]:
                raise QualificationError(f"case identity mismatch: {candidate_id}")
            if case["candidate_rtl_path"] != f"{source_id}/{name}.sv" or case["testbench_path"] != f"{source_id}/testbench.sv":
                raise QualificationError(f"case path mismatch: {candidate_id}")
            expected_attempt = 1 if offset == 0 else offset + 1
            if case["attempt"] != expected_attempt:
                raise QualificationError(f"case attempt mismatch: {candidate_id}")
            if case["support_files"] != [] or case["reference_rtl_supplied"] is not False:
                raise QualificationError(f"case supplied private/support content: {candidate_id}")
            if case["candidate_sha256"] != sha256_file(candidate_path) or case["testbench_sha256"] != sha256_file(testbench_path):
                raise QualificationError(f"case artifact hash mismatch: {candidate_id}")
            if _public_rtl_errors(candidate_path):
                raise QualificationError(f"qualification candidate contains forbidden content: {candidate_id}")
            if manifest != {
                "schema_version": "rtl_candidate_manifest_v0.1",
                "candidate_id": candidate_id,
                "task_id": source["task_id"],
                "source_id": source_id,
                "attempt": expected_attempt,
                "top_module": source["top_module"],
                "testbench_top": "tb",
                "candidate_rtl_path": f"{source_id}/{name}.sv",
                "testbench_path": f"{source_id}/testbench.sv",
                "support_files": [],
                "simulation_result_contract": "mismatch_count_v1",
                "requested_checks": {"compile": True, "simulation": True, "lint": False, "synthesis": False},
            }:
                raise QualificationError(f"candidate manifest binding mismatch: {candidate_id}")

    manifest_hash = sha256_file(input_root / "candidate_manifest.jsonl")
    workspace_hash = _workspace_tree_sha256(workspace)
    if manifest_hash != preparation["candidate_manifest_sha256"] or workspace_hash != preparation["workspace_tree_sha256"]:
        raise QualificationError("prepared input hash does not match preparation report")

    authorization = _load_json(authorization_path)
    if not isinstance(authorization, dict):
        raise QualificationError("authorization must be an object")
    auth_expected = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "run_id": preparation.get("run_id", RUN_ID),
        "status": "authorized_once",
        "authorization_scope": "qualification_only",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": hashes["ids_sha256"],
        "correction_manifest_sha256": correction_hash,
        "correction_version": correction_version,
        "selected_source_ids": source_ids,
        "case_count": expected_case_count,
        "positive_case_count": expected_positive_count,
        "negative_case_count": expected_negative_count,
        "qualification_status_before": "pending_isolated_qualification",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authorized_invocations": 1,
    }
    for key, expected in auth_expected.items():
        if authorization.get(key) != expected:
            raise QualificationError(f"authorization mismatch: {key}")
    if authorization.get("candidate_manifest_sha256") != manifest_hash or authorization.get("workspace_tree_sha256") != workspace_hash:
        raise QualificationError("authorization input hash mismatch")
    runner = authorization.get("runner")
    if not isinstance(runner, dict) or {
        runner.get("profile"), runner.get("runtime"), runner.get("runtime_mode"), runner.get("rootless"),
        runner.get("image_id"), runner.get("rtlbench_commit"), runner.get("network_policy"), runner.get("runtime_user"),
    } != {"pilot-docker", "docker", "rootful-daemon", False, expected_image_id, expected_rtlbench_commit, "none", "65532:65532"}:
        raise QualificationError("authorization runner binding mismatch")
    expected_input = str(input_root.resolve())
    expected_output = str((input_root.parent / "staged/candidate_evidence.jsonl").resolve())
    expected_command = [
        str((rtlbench_root or Path("/home/armmy-server/apps/RTLBench")) / ".venv/bin/python"),
        "runner/run_isolated.py", "--profile", "pilot-docker", "--acknowledge-rootful-runtime",
        "--image", expected_image_id, "--input", expected_input, "--output", expected_output,
    ]
    if authorization.get("qualification_input") != expected_input or authorization.get("exact_command") != expected_command:
        raise QualificationError("authorization command or input path mismatch")

    if rtlbench_root is not None and rtlspecializer_root is not None:
        current_attestations = {
            "rtlbench": repository_attestation(rtlbench_root),
            "rtlspecializer": repository_attestation(rtlspecializer_root),
        }
        if authorization.get("repository_attestations") != current_attestations:
            raise QualificationError("repository attestation changed after authorization")
        if current_attestations["rtlbench"]["head"] != expected_rtlbench_commit or current_attestations["rtlbench"]["branch"] != "main":
            raise QualificationError("RTLBench repository identity is not pinned")
        if current_attestations["rtlspecializer"]["head"] != expected_rtlspecializer_commit or current_attestations["rtlspecializer"]["branch"] != "main":
            raise QualificationError("RTLSpecializer repository identity is not pinned")

    forbidden_output_names = {
        "candidate_evidence.jsonl", "candidate_evidence.jsonl.runner.json",
        "candidate_evidence.jsonl.rtlbench-partial", "generation_attempts.jsonl",
        "run_instructions.md", "verification_plan.jsonl",
    }
    if any(path.name in forbidden_output_names for path in qualification_root.rglob("*")):
        raise QualificationError("qualification attempt contains execution or handoff output before execution")
    if any(path.name.casefold() == "reference.sv" for path in qualification_root.rglob("*")):
        raise QualificationError("qualification attempt contains reference RTL")

    result = {
        "schema_version": "rtl_asset_qualification_preflight_v0.1",
        "run_id": preparation.get("run_id", RUN_ID),
        "status": "ready_for_isolated_execution",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": hashes["ids_sha256"],
        "selection_report_sha256": hashes["selection_sha256"],
        "correction_manifest_sha256": correction_hash,
        "correction_static_report_sha256": static_hash,
        "inventory_sha256": hashes["inventory_sha256"],
        "split_sha256": hashes["split_sha256"],
        "authoring_manifest_sha256": authoring_hash,
        "qualification_manifest_sha256": manifest_hash,
        "workspace_tree_sha256": workspace_hash,
        "permission_contract": {
            "passed": True,
            "uid": permission_uid,
            "gid": permission_gid,
            "qualification_file_count": permission_file_count,
        },
        "selected_source_ids": source_ids,
        "selected_task_count": len(source_ids),
        "candidate_case_count": expected_case_count,
        "positive_case_count": expected_positive_count,
        "negative_case_count": expected_negative_count,
        "staged_output_path": str((qualification_root / "staged").resolve()),
        "staged_output_ready": True,
        "qualification_status_before": "pending_isolated_qualification",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "staged_runner_entries": ["candidate_manifest.jsonl", "workspace"],
        "instructions_staged": False,
        "verification_plan_staged": False,
        "authorization_valid": True,
        "repository_identity_valid": True,
        "evidence_present_before_execution": False,
        "attempt_history_present_before_execution": False,
        "errors": [],
    }
    _write_json(report_output, result)
    return result


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout


def repository_attestation(repo: Path) -> dict[str, Any]:
    status = _git(repo, "status", "--porcelain=v1").splitlines()
    tracked_diff = _git(repo, "diff", "--binary").encode("utf-8")
    staged_diff = _git(repo, "diff", "--cached", "--binary").encode("utf-8")
    files = _git(repo, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
    records: list[bytes] = []
    for relative in sorted(item for item in files if item):
        path = repo / relative
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise QualificationError(f"repository attestation found non-regular path: {relative}")
        records.append(relative.encode() + b"\0" + sha256_file(path).encode() + b"\n")
    return {
        "head": _git(repo, "rev-parse", "HEAD").strip(),
        "branch": _git(repo, "branch", "--show-current").strip(),
        "clean": not status,
        "status_lines": status,
        "status_sha256": hashlib.sha256(("\n".join(status) + "\n").encode()).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(tracked_diff).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(staged_diff).hexdigest(),
        "working_tree_files_sha256": hashlib.sha256(b"".join(records)).hexdigest(),
    }


def create_authorization(
    *,
    output_path: Path,
    preparation_report: dict[str, Any],
    qualification_input: Path,
    rtlbench_root: Path,
    rtlspecializer_root: Path,
    rtlbench_commit: str = RTLBench_COMMIT,
    rtlspecializer_commit: str = RTLSPECIALIZER_BASE_COMMIT,
    image_id: str = IMAGE_ID,
) -> dict[str, Any]:
    selected_source_ids = preparation_report.get("selected_source_ids")
    correction_version = preparation_report.get("correction_version")
    case_count = preparation_report.get("case_count")
    positive_case_count = preparation_report.get("positive_case_count")
    negative_case_count = preparation_report.get("negative_case_count")
    if not isinstance(selected_source_ids, list) or not selected_source_ids:
        raise QualificationError("preparation report has no selected source IDs")
    expected_preparation = {
        "run_id": preparation_report.get("run_id", RUN_ID),
        "case_count": case_count,
        "positive_case_count": positive_case_count,
        "negative_case_count": negative_case_count,
        "selected_source_ids": selected_source_ids,
        "correction_version": correction_version,
        "qualification_status_before": "pending_isolated_qualification",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "teacher_generation_allowed": False,
    }
    for key, expected in expected_preparation.items():
        if preparation_report.get(key) != expected:
            raise QualificationError(f"preparation report binding mismatch: {key}")
    for key in (
        "selection_ids_sha256",
        "selection_report_sha256",
        "correction_manifest_sha256",
        "inventory_sha256",
        "split_sha256",
        "candidate_manifest_sha256",
        "workspace_tree_sha256",
    ):
        value = preparation_report.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise QualificationError(f"preparation report lacks a valid hash: {key}")
    _require_directory(qualification_input)
    staged_output = _require_empty_staged_output(qualification_input.parent)
    candidate_manifest = qualification_input / "candidate_manifest.jsonl"
    workspace = qualification_input / "workspace"
    if sha256_file(candidate_manifest) != preparation_report["candidate_manifest_sha256"]:
        raise QualificationError("qualification input manifest hash changed before authorization")
    if _workspace_tree_sha256(workspace) != preparation_report["workspace_tree_sha256"]:
        raise QualificationError("qualification input workspace hash changed before authorization")
    if not IMAGE_RE.fullmatch(image_id):
        raise QualificationError("authorization image must be a local immutable image ID")
    if rtlbench_commit != RTLBench_COMMIT or not re.fullmatch(r"[0-9a-f]{40}", rtlbench_commit):
        raise QualificationError("authorization RTLBench commit mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", rtlspecializer_commit):
        raise QualificationError("authorization RTLSpecializer commit is invalid")
    rtlbench_attestation = repository_attestation(rtlbench_root)
    rtlspecializer_attestation = repository_attestation(rtlspecializer_root)
    if rtlbench_attestation["head"] != rtlbench_commit:
        raise QualificationError("RTLBench repository HEAD does not match authorization commit")
    if rtlbench_attestation["branch"] != "main":
        raise QualificationError("RTLBench repository is not on main")
    if rtlspecializer_attestation["head"] != rtlspecializer_commit:
        raise QualificationError("RTLSpecializer repository HEAD does not match authorization commit")
    if rtlspecializer_attestation["branch"] != "main":
        raise QualificationError("RTLSpecializer repository is not on main")
    report = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "run_id": preparation_report.get("run_id", RUN_ID),
        "status": "authorized_once",
        "authorization_scope": "qualification_only",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": BASE_SPLIT_SHA256,
        "selection_ids_sha256": preparation_report["selection_ids_sha256"],
        "selection_report_sha256": preparation_report["selection_report_sha256"],
        "correction_manifest_sha256": preparation_report["correction_manifest_sha256"],
        "inventory_sha256": preparation_report["inventory_sha256"],
        "split_sha256": preparation_report["split_sha256"],
        "candidate_manifest_sha256": preparation_report["candidate_manifest_sha256"],
        "workspace_tree_sha256": preparation_report["workspace_tree_sha256"],
        "correction_version": correction_version,
        "selected_source_ids": selected_source_ids,
        "case_count": case_count,
        "positive_case_count": positive_case_count,
        "negative_case_count": negative_case_count,
        "qualification_status_before": "pending_isolated_qualification",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "runner": {
            "profile": "pilot-docker",
            "runtime": "docker",
            "runtime_mode": "rootful-daemon",
            "rootless": False,
            "image_identity_kind": "local-image-id",
            "image_id": image_id,
            "rtlbench_commit": rtlbench_commit,
            "runtime_user": "65532:65532",
            "network_policy": "none",
        },
        "qualification_input": str(qualification_input.resolve()),
        "staged_output_path": str(staged_output.resolve()),
        "staged_output_ready": True,
        "exact_command": [
            str(rtlbench_root / ".venv/bin/python"),
            "runner/run_isolated.py",
            "--profile", "pilot-docker",
            "--acknowledge-rootful-runtime",
            "--image", image_id,
            "--input", str(qualification_input.resolve()),
            "--output", str((qualification_input.parent / "staged/candidate_evidence.jsonl").resolve()),
        ],
        "repository_attestations": {
            "rtlbench": rtlbench_attestation,
            "rtlspecializer": rtlspecializer_attestation,
        },
        "smoke_review_exceptions": SMOKE_REVIEW_EXCEPTIONS if correction_version == CORRECTION_VERSION else [],
        "candidate_generation_repeated": False,
        "normalization_performed": False,
        "teacher_generation_performed": False,
        "packaging_performed": False,
        "authorized_invocations": 1,
        "errors": [],
    }
    _write_json(output_path, report)
    return report


__all__ = [
    "AUTHORING_ROW_SCHEMA_VERSION",
    "AUTHORIZATION_SCHEMA_VERSION",
    "BATCH20_SOURCE_IDS",
    "CORRECTION_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "IMAGE_ID",
    "QUALIFICATION_CASE_SCHEMA_VERSION",
    "REPORT_SCHEMA_VERSION",
    "RUN_ID",
    "create_authorization",
    "aggregate_qualification_evidence",
    "prepare_qualification_input",
    "validate_prepared_qualification",
    "repository_attestation",
    "_workspace_tree_sha256",
]
