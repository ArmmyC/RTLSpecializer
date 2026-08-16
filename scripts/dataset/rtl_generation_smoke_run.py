"""Control-plane gates for the bounded five-task assetfix_v002 run.

This module deliberately does not call an LLM, Docker, RTLBench, a compiler,
or a simulator.  It binds public normalization packets to the pinned source
and split, joins a validated public response to corrected private assets, and
validates qualification evidence produced by a separately controlled
RTLBench execution.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable

from scripts.dataset.rtl_generation_asset_corrections import (
    CORRECTION_VERSION,
    load_correction_manifest,
)
from scripts.dataset.rtl_generation_inventory import (
    INVENTORY_SCHEMA_VERSION,
    _load_inventory,
    _sha256_file,
    source_tree_sha256,
    validate_generation_split,
)
from scripts.dataset.rtl_generation_preparation import (
    VERIFICATION_ASSET_SCHEMA_VERSION,
    assemble_generation_inputs,
    export_generation_normalization_batches,
    validate_generation_normalized_batch,
)


SMOKE_RUN_SCHEMA_VERSION = "rtl_generation_smoke_run_v0.1"
QUALIFICATION_REPORT_SCHEMA_VERSION = "rtl_verification_asset_qualification_v0.1"
QUALIFICATION_SIDECAR_SCHEMA_VERSION = "rtlbench_mutation_runner_identity_v0.1"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
FROZEN_SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
EXPECTED_SOURCE_IDS = (
    "Prob001_zero",
    "Prob020_mt2015_eq2",
    "Prob071_always_casez",
    "Prob048_m2014_q4c",
    "Prob079_fsm3onehot",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    ".local_data",
    "private_assets",
    "reference.sv",
    "_ref.sv",
    "refmodule",
)
_QUALIFICATION_SIMULATION_DIAGNOSTIC_RE = re.compile(
    r"^simulation: returncode=0 Mismatches: [0-9]+ "
    r"<workspace><path> \$finish called at [0-9]+ \([0-9]+s\)$"
)
_QUALIFICATION_COMPILE_WARNING_RE = re.compile(
    r"^compile: returncode=0 <workspace><path> warning: [^/\r\n]+$"
)


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {index} is not an object")
        rows.append(value)
    return rows


def _write_exclusive_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def _write_exclusive_text(path: Path, value: str) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(value.encode("utf-8"))
    os.chmod(path, 0o600)


def _workspace_tree_sha256(root: Path) -> str:
    """Match RTLBench's path-independent workspace tree hash."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("workspace root must be a regular directory")
    records: list[bytes] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError("workspace contains a symlink")
            records.append(b"D\0" + path.relative_to(root).as_posix().encode("utf-8") + b"\n")
        for name in files:
            path = Path(current) / name
            metadata = path.lstat()
            if path.is_symlink():
                raise ValueError("workspace contains a symlink")
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("workspace contains a non-regular file")
            records.append(
                b"F\0"
                + path.relative_to(root).as_posix().encode("utf-8")
                + b"\0"
                + _sha256_file(path).encode("ascii")
                + b"\n"
            )
    return hashlib.sha256(b"".join(records)).hexdigest()


def _safe_relative(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ValueError("qualification artifact path must be relative POSIX text")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("qualification artifact path contains unsafe components")
    path = root.joinpath(*parts)
    resolved_root = root.resolve()
    if path.resolve() != resolved_root and resolved_root not in path.resolve().parents:
        raise ValueError("qualification artifact path escapes workspace")
    if path.is_symlink() or not path.is_file():
        raise ValueError("qualification artifact is not a regular file")
    return path


def _file_marker_errors(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"qualification artifact is not UTF-8: {path.name}"]
    lowered = text.casefold()
    return [f"private marker in qualification artifact: {path.name}" for marker in _PRIVATE_MARKERS if marker in lowered]


def _expected_ids(source_ids: Iterable[str] | None) -> list[str]:
    values = list(source_ids) if source_ids is not None else list(EXPECTED_SOURCE_IDS)
    if values != list(EXPECTED_SOURCE_IDS):
        raise ValueError("v002 smoke selection must use the exact five source IDs in the recorded order")
    return values


def validate_smoke_binding(
    *,
    source_root: Path,
    inventory_path: Path,
    base_inventory_path: Path,
    split_path: Path,
    correction_manifest_path: Path,
    correction_report_path: Path,
    source_ids: Iterable[str] | None = None,
    expected_source_commit: str = SOURCE_COMMIT,
    expected_source_tree_sha256: str = SOURCE_TREE_SHA256,
    expected_split_sha256: str = FROZEN_SPLIT_SHA256,
    expected_correction_version: str = CORRECTION_VERSION,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    selected_ids: list[str]
    try:
        selected_ids = _expected_ids(source_ids)
        inventory = _load_inventory(inventory_path)
        base_inventory = _load_inventory(base_inventory_path)
        correction_report = _load_json(correction_report_path)
        correction_rows, correction_errors = load_correction_manifest(
            correction_manifest_path,
            correction_manifest_path.parent,
        )
        errors.extend(correction_errors)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    if not isinstance(correction_report, dict):
        errors.append("correction report must be a JSON object")
        correction_report = {}

    if expected_source_commit != SOURCE_COMMIT or not _COMMIT_RE.fullmatch(expected_source_commit):
        errors.append("unexpected source commit binding")
    if expected_source_tree_sha256 != SOURCE_TREE_SHA256 or not _SHA256_RE.fullmatch(expected_source_tree_sha256):
        errors.append("unexpected source-tree binding")
    if expected_split_sha256 != FROZEN_SPLIT_SHA256 or not _SHA256_RE.fullmatch(expected_split_sha256):
        errors.append("unexpected frozen-split binding")
    if expected_correction_version != CORRECTION_VERSION:
        errors.append("unexpected correction version")

    actual_tree_hash, symlinks, tree_errors = source_tree_sha256(source_root)
    errors.extend(tree_errors)
    if symlinks:
        errors.append("source tree contains symlinks")
    if actual_tree_hash != expected_source_tree_sha256:
        errors.append("source-tree hash mismatch")
    if _sha256_file(split_path) != expected_split_sha256:
        errors.append("frozen split hash mismatch")
    split_report, split_code = validate_generation_split(base_inventory_path, split_path, expected_seed=7)
    if split_code:
        errors.extend(split_report.get("errors", []))
    if correction_report.get("source_commit") != expected_source_commit:
        errors.append("correction report source commit mismatch")
    if correction_report.get("source_tree_sha256") != expected_source_tree_sha256:
        errors.append("correction report source-tree hash mismatch")
    if correction_report.get("base_split_sha256") != expected_split_sha256:
        errors.append("correction report split hash mismatch")
    if correction_report.get("correction_version") != expected_correction_version:
        errors.append("correction report version mismatch")
    if correction_report.get("verification_readiness") != "executable_ready":
        errors.append("correction report is not executable_ready")
    if correction_report.get("dependency_closure_passed") is not True:
        errors.append("correction dependency closure is not passed")

    inventory_by_id = {row.get("source_id"): row for row in inventory}
    base_by_id = {row.get("source_id"): row for row in base_inventory}
    split_values = split_report.get("split_counts", {})
    split_manifest = _load_json(split_path)
    if not isinstance(split_manifest, dict):
        errors.append("frozen split manifest must be a JSON object")
        split_manifest = {}
    splits = split_manifest.get("splits")
    if not isinstance(splits, dict):
        errors.append("frozen split manifest splits must be an object")
        splits = {}
    train_ids = splits.get("train", [])
    for source_id in selected_ids:
        row = inventory_by_id.get(source_id)
        if row is None:
            errors.append(f"selected source ID is absent from inventory: {source_id}")
            continue
        if source_id not in train_ids:
            errors.append(f"selected source ID is not in the frozen train split: {source_id}")
        if row.get("verification_readiness") != "executable_ready":
            errors.append(f"selected source ID is not executable_ready: {source_id}")
        base = base_by_id.get(source_id)
        if base is None:
            errors.append(f"selected source ID is absent from base inventory: {source_id}")
        elif row.get("source_commit") != base.get("source_commit"):
            errors.append(f"source commit changed in overlay inventory: {source_id}")

    correction_by_id = {row.get("source_id"): row for row in correction_rows}
    if set(correction_by_id) != set(selected_ids):
        errors.append("correction manifest IDs do not equal the five selected IDs")
    for source_id in selected_ids:
        row = correction_by_id.get(source_id)
        if row is None:
            continue
        if row.get("split") != "train":
            errors.append(f"correction row is not train: {source_id}")
        if row.get("verification_readiness") != "executable_ready":
            errors.append(f"correction row is not executable_ready: {source_id}")
        if row.get("correction_version") != expected_correction_version:
            errors.append(f"correction version mismatch: {source_id}")
        if row.get("dependency_closure") != "passed":
            errors.append(f"correction dependency closure failed: {source_id}")

    report = {
        "schema_version": SMOKE_RUN_SCHEMA_VERSION,
        "source_commit": expected_source_commit,
        "source_tree_sha256": expected_source_tree_sha256,
        "frozen_split_sha256": expected_split_sha256,
        "correction_version": expected_correction_version,
        "inventory_sha256": _sha256_file(inventory_path),
        "base_inventory_sha256": _sha256_file(base_inventory_path),
        "correction_manifest_sha256": _sha256_file(correction_manifest_path),
        "selected_source_ids": selected_ids,
        "selected_count": len(selected_ids),
        "train_count": split_values.get("train"),
        "errors": sorted(set(errors)),
    }
    return {**report, "ok": not errors}, 0 if not errors else 1


def prepare_normalization_smoke_run(
    *,
    input_path: Path,
    source_root: Path,
    inventory_path: Path,
    base_inventory_path: Path,
    split_path: Path,
    source_ids_path: Path,
    correction_manifest_path: Path,
    correction_report_path: Path,
    output_dir: Path,
    private_output_dir: Path,
    attestation_output: Path,
) -> tuple[dict[str, Any], int]:
    try:
        source_ids = [line.strip() for line in source_ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        binding, binding_code = validate_smoke_binding(
            source_root=source_root,
            inventory_path=inventory_path,
            base_inventory_path=base_inventory_path,
            split_path=split_path,
            correction_manifest_path=correction_manifest_path,
            correction_report_path=correction_report_path,
            source_ids=source_ids,
        )
        if binding_code:
            return {"ok": False, "stage": "binding", "binding": binding}, 1
        export_result, export_code = export_generation_normalization_batches(
            input_path,
            output_dir,
            private_output_dir,
            batch_size=5,
            source_commit=SOURCE_COMMIT,
            source_ids=source_ids,
            correction_manifest=correction_manifest_path,
            correction_root=correction_manifest_path.parent,
        )
        if export_code:
            return {"ok": False, "stage": "export", "binding": binding, "export": export_result}, 1
        batch_paths = sorted(output_dir.glob("batch_*.json"))
        if len(batch_paths) != 1:
            raise ValueError("normalization smoke export must contain exactly one batch")
        batch = _load_json(batch_paths[0])
        rows = batch.get("rows") if isinstance(batch, dict) else None
        if not isinstance(rows, list) or len(rows) != 5:
            raise ValueError("normalization smoke batch must contain exactly five rows")
        actual_ids = [row.get("source_id") for row in rows]
        if actual_ids != source_ids:
            raise ValueError("normalization batch row order differs from the selected source-ID file")
        public_bytes = batch_paths[0].read_bytes()
        public_text = public_bytes.decode("utf-8")
        lowered = public_text.casefold()
        for marker in ("refmodule", "reference.sv", "testbench.sv", ".local_data", "/home/", "/tmp/", "private_assets"):
            if marker in lowered:
                raise ValueError(f"public normalization batch contains a forbidden marker: {marker}")
        assets_path = private_output_dir / "verification_assets.jsonl"
        if not assets_path.is_file():
            raise ValueError("normalization export did not create its private asset manifest")
        attestation = {
            **binding,
            "schema_version": SMOKE_RUN_SCHEMA_VERSION,
            "batch_sha256": _sha256_file(batch_paths[0]),
            "batch_row_count": len(rows),
            "batch_source_ids": actual_ids,
            "private_assets_sha256": _sha256_file(assets_path),
            "public_reference_exposed": False,
            "public_testbench_exposed": False,
            "errors": [],
        }
        _write_exclusive_json(attestation_output, attestation)
        return {
            "ok": True,
            "stage": "export",
            "binding": binding,
            "export": export_result,
            "attestation": {"path": attestation_output.as_posix(), **attestation},
        }, 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "stage": "export", "errors": [str(exc)]}, 1


def validate_normalization_response(
    *,
    raw_batch_path: Path,
    response_path: Path,
    private_assets_path: Path,
    expected_source_ids: Iterable[str] = EXPECTED_SOURCE_IDS,
) -> tuple[dict[str, Any], int]:
    try:
        expected_ids = _expected_ids(expected_source_ids)
        raw_batch = _load_json(raw_batch_path)
        response = _load_json(response_path)
        raw_rows = raw_batch.get("rows") if isinstance(raw_batch, dict) else None
        response_rows = response.get("rows") if isinstance(response, dict) else None
        errors: list[str] = []
        if not isinstance(raw_rows, list) or len(raw_rows) != 5:
            errors.append("raw normalization batch must contain five rows")
            raw_rows = []
        if [row.get("source_id") if isinstance(row, dict) else None for row in raw_rows] != expected_ids:
            errors.append("raw normalization batch IDs differ from the exact smoke selection")
        if not isinstance(response, dict) or set(response) != {"rows"}:
            errors.append("normalization response must contain only a top-level rows field")
        if not isinstance(response_rows, list) or len(response_rows) != 5:
            errors.append("normalization response must contain exactly five rows")
            response_rows = []
        if [row.get("source_id") for row in response_rows if isinstance(row, dict)] != expected_ids:
            errors.append("normalization response IDs or row order differ from the packet")
        validation, validation_code = validate_generation_normalized_batch(
            raw_batch_path,
            response_path,
            private_assets_path,
            require_response_object=True,
        )
        errors.extend(validation.get("errors", []))
        result = {
            "schema_version": SMOKE_RUN_SCHEMA_VERSION,
            "raw_batch_sha256": _sha256_file(raw_batch_path),
            "response_sha256": _sha256_file(response_path),
            "row_count": len(response_rows),
            "source_ids": [row.get("source_id") for row in response_rows if isinstance(row, dict)],
            "validation": validation,
            "errors": sorted(set(errors)),
        }
        return {**result, "ok": not errors and validation_code == 0}, 0 if not errors and validation_code == 0 else 1
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1


def assemble_smoke_inputs(
    *,
    normalized_path: Path,
    private_assets_path: Path,
    tasks_output: Path,
    assets_output: Path,
    correction_manifest_path: Path,
    correction_report_path: Path,
    attestation_output: Path,
) -> tuple[dict[str, Any], int]:
    try:
        result, code = assemble_generation_inputs(
            normalized_path,
            private_assets_path,
            tasks_output,
            assets_output,
        )
        if code:
            return {"ok": False, "stage": "assembly", "assembly": result}, 1
        tasks = _load_jsonl(tasks_output)
        assets = _load_jsonl(assets_output)
        corrections, correction_errors = load_correction_manifest(
            correction_manifest_path,
            correction_manifest_path.parent,
        )
        if correction_errors:
            return {"ok": False, "stage": "assembly", "errors": correction_errors}, 1
        expected_ids = list(EXPECTED_SOURCE_IDS)
        if [row.get("source_id") for row in tasks] != expected_ids:
            raise ValueError("assembled task source-ID order differs from the five-task selection")
        if len(tasks) != 5 or len(assets) != 5:
            raise ValueError("assembly must contain exactly five tasks and five assets")
        correction_by_id = {row["source_id"]: row for row in corrections}
        if set(correction_by_id) != set(EXPECTED_SOURCE_IDS):
            raise ValueError("correction manifest IDs do not equal the five-task selection")
        private_root = private_assets_path.parent
        if private_root.is_symlink() or not private_root.is_dir():
            raise ValueError("private asset root must be a regular directory")
        attested_rows: list[dict[str, Any]] = []
        for task, asset in zip(tasks, assets):
            source_id = task.get("source_id")
            correction = correction_by_id.get(source_id)
            if correction is None:
                raise ValueError(f"missing correction row for source ID: {source_id}")
            if asset.get("source_id") != source_id or asset.get("task_id") != task.get("task_id"):
                raise ValueError(f"task/asset identity mismatch: {source_id}")
            if asset.get("verification_readiness") != "executable_ready":
                raise ValueError(f"asset is not executable_ready: {source_id}")
            if asset.get("support_files") != []:
                raise ValueError(f"support files are not empty: {source_id}")
            if correction.get("correction_version") != CORRECTION_VERSION:
                raise ValueError(f"correction version mismatch: {source_id}")
            if correction.get("dependency_closure") != "passed":
                raise ValueError(f"correction dependency closure failed: {source_id}")
            hashes = asset.get("input_hashes") if isinstance(asset.get("input_hashes"), dict) else {}
            if hashes.get("testbench_sha256") != correction.get("corrected_testbench_sha256"):
                raise ValueError(f"corrected testbench hash mismatch: {source_id}")
            if hashes.get("reference_rtl_sha256") != correction.get("original_reference_rtl_sha256"):
                raise ValueError(f"original reference hash changed: {source_id}")
            try:
                testbench_path = _safe_relative(private_root, asset.get("testbench_path"))
            except ValueError as exc:
                raise ValueError(f"corrected testbench path is invalid: {source_id}: {exc}") from exc
            if _sha256_file(testbench_path) != hashes.get("testbench_sha256"):
                raise ValueError(f"assembled testbench bytes do not match the asset hash: {source_id}")
            text = testbench_path.read_text(encoding="utf-8").casefold()
            if any(marker in text for marker in _PRIVATE_MARKERS):
                raise ValueError(f"corrected testbench contains a forbidden private marker: {source_id}")
            if len(re.findall(r"\bmodule\s+tb\b", text)) != 1 or not re.search(r"\btopmodule\b", text):
                raise ValueError(f"corrected testbench does not expose the expected public harness: {source_id}")
            attested_rows.append({
                "source_id": source_id,
                "task_id": task.get("task_id"),
                "correction_version": correction.get("correction_version"),
                "corrected_testbench_sha256": hashes.get("testbench_sha256"),
                "original_reference_rtl_sha256": hashes.get("reference_rtl_sha256"),
                "support_file_count": 0,
                "verification_readiness": asset.get("verification_readiness"),
            })
        report = _load_json(correction_report_path)
        if not isinstance(report, dict):
            raise ValueError("correction report must be a JSON object")
        if report.get("correction_version") != CORRECTION_VERSION:
            raise ValueError("correction report version mismatch")
        attestation = {
            "schema_version": SMOKE_RUN_SCHEMA_VERSION,
            "correction_version": CORRECTION_VERSION,
            "correction_manifest_sha256": _sha256_file(correction_manifest_path),
            "correction_report_sha256": _sha256_file(correction_report_path),
            "normalized_sha256": _sha256_file(normalized_path),
            "tasks_sha256": _sha256_file(tasks_output),
            "assets_sha256": _sha256_file(assets_output),
            "row_count": 5,
            "rows": attested_rows,
            "reference_exposed": False,
            "errors": [],
        }
        _write_exclusive_json(attestation_output, attestation)
        return {"ok": True, "assembly": result, "attestation": attestation}, 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "stage": "assembly", "errors": [str(exc)]}, 1


def _status_passes(value: Any) -> bool:
    return isinstance(value, dict) and value.get("attempted") is True and value.get("passed") is True and value.get("reason") is None


def _qualification_diagnostic_errors(value: Any) -> list[str]:
    """Allow only sanitized, benign RTLBench output for passing rows."""

    if not isinstance(value, list):
        return ["diagnostics must be an array"]
    errors: list[str] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, str):
            errors.append(f"diagnostic {index} is not a string")
            continue
        if len(item.encode("utf-8")) > 4096:
            errors.append(f"diagnostic {index} exceeds 4096 bytes")
            continue
        lowered = item.casefold()
        if any(marker in lowered for marker in _PRIVATE_MARKERS):
            errors.append(f"diagnostic {index} contains a private marker")
            continue
        if (
            _QUALIFICATION_SIMULATION_DIAGNOSTIC_RE.fullmatch(item)
            or _QUALIFICATION_COMPILE_WARNING_RE.fullmatch(item)
        ):
            continue
        errors.append(f"diagnostic {index} is not an allowed sanitized result")
    return errors


def _qualification_workspace_files(root: Path) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    files: set[str] = set()
    if root.is_symlink() or not root.is_dir():
        return files, ["qualification workspace must be a regular directory"]
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        for name in directories:
            path = Path(current) / name
            if path.is_symlink():
                errors.append("qualification workspace contains a symlink")
        for name in names:
            path = Path(current) / name
            try:
                metadata = path.lstat()
            except OSError:
                errors.append("qualification workspace entry cannot be inspected")
                continue
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                errors.append("qualification workspace contains a non-regular file")
                continue
            files.add(path.relative_to(root).as_posix())
            errors.extend(_file_marker_errors(path))
    return files, sorted(set(errors))


def validate_asset_qualification(
    *,
    tasks_path: Path,
    assets_path: Path,
    correction_manifest_path: Path,
    correction_report_path: Path,
    qualification_manifest_path: Path,
    qualification_evidence_path: Path,
    qualification_sidecar_path: Path,
    workspace_root: Path,
    report_output: Path,
    expected_image_id: str | None = None,
    expected_rtlbench_commit: str = "fcad47eb03e469097432229e1285b9239fd23a00",
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    try:
        tasks = _load_jsonl(tasks_path)
        assets = _load_jsonl(assets_path)
        corrections, correction_errors = load_correction_manifest(correction_manifest_path, correction_manifest_path.parent)
        manifest_rows = _load_jsonl(qualification_manifest_path)
        evidence_rows = _load_jsonl(qualification_evidence_path)
        sidecar = _load_json(qualification_sidecar_path)
        correction_report = _load_json(correction_report_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    if not isinstance(correction_report, dict):
        errors.append("qualification correction report must be a JSON object")
        correction_report = {}
    errors.extend(correction_errors)
    expected_ids = list(EXPECTED_SOURCE_IDS)
    tasks_by_id = {row.get("source_id"): row for row in tasks}
    assets_by_id = {row.get("source_id"): row for row in assets}
    correction_by_id = {row.get("source_id"): row for row in corrections}
    if len(tasks) != 5 or len(assets) != 5:
        errors.append("qualification requires exactly five assembled tasks and assets")
    if [row.get("source_id") for row in tasks] != expected_ids or [row.get("source_id") for row in assets] != expected_ids:
        errors.append("qualification task IDs do not equal the five selected source IDs")
    if set(correction_by_id) != set(expected_ids):
        errors.append("qualification correction IDs do not equal the five selected source IDs")
    if correction_report.get("source_commit") != SOURCE_COMMIT:
        errors.append("qualification correction report source commit mismatch")
    if correction_report.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        errors.append("qualification correction report source-tree hash mismatch")
    if correction_report.get("base_split_sha256") != FROZEN_SPLIT_SHA256:
        errors.append("qualification correction report frozen split hash mismatch")
    if correction_report.get("correction_version") != CORRECTION_VERSION:
        errors.append("qualification correction report version mismatch")
    if correction_report.get("verification_readiness") != "executable_ready":
        errors.append("qualification correction report is not executable_ready")

    workspace_files, workspace_errors = _qualification_workspace_files(workspace_root)
    errors.extend(workspace_errors)
    declared_files: set[str] = set()
    manifest_by_mutation: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(manifest_rows, 1):
        required = {
            "schema_version", "mutation_id", "source_id", "top_module",
            "original_rtl_path", "mutated_rtl_path", "repaired_rtl_path",
            "testbench_path", "support_files", "mutation_type", "mutated_signal",
            "changed_location", "requested_checks",
        }
        if set(row) != required:
            errors.append(f"qualification manifest row {index} has an invalid field set")
            continue
        if row.get("schema_version") != "rtl_mutation_manifest_v0.1":
            errors.append(f"qualification manifest row {index} has the wrong schema")
        mutation_id = row.get("mutation_id")
        if not isinstance(mutation_id, str) or mutation_id in manifest_by_mutation:
            errors.append(f"qualification manifest row {index} has a duplicate mutation ID")
            continue
        manifest_by_mutation[mutation_id] = row
        source_id = row.get("source_id")
        task = tasks_by_id.get(source_id)
        asset = assets_by_id.get(source_id)
        correction = correction_by_id.get(source_id)
        if task is None or asset is None or correction is None:
            errors.append(f"qualification manifest row {index} has an unknown source ID")
            continue
        if row.get("top_module") != task.get("top_module"):
            errors.append(f"qualification top module mismatch: {source_id}")
        if row.get("support_files") != []:
            errors.append(f"qualification support files must be empty: {source_id}")
        checks = row.get("requested_checks")
        if checks != {"compile": True, "lint": False, "simulation": True, "synthesis": False, "equivalence": False, "activity": False}:
            errors.append(f"qualification requested checks are not compile/simulation only: {source_id}")
        asset_hashes = asset.get("input_hashes") if isinstance(asset.get("input_hashes"), dict) else {}
        asset_hash = asset_hashes.get("testbench_sha256")
        for field in ("original_rtl_path", "mutated_rtl_path", "repaired_rtl_path", "testbench_path"):
            try:
                path = _safe_relative(workspace_root, row.get(field))
            except ValueError as exc:
                errors.append(f"qualification {field} invalid for {source_id}: {exc}")
                continue
            declared_files.add(path.relative_to(workspace_root).as_posix())
        try:
            qualification_testbench = _safe_relative(workspace_root, row.get("testbench_path"))
            if _sha256_file(qualification_testbench) != asset_hash:
                errors.append(f"qualification testbench hash is not the assembled corrected hash: {source_id}")
        except ValueError:
            pass
        support_files = row.get("support_files")
        if not isinstance(support_files, list):
            support_files = []
        for path_value in support_files:
            try:
                path = _safe_relative(workspace_root, path_value)
                declared_files.add(path.relative_to(workspace_root).as_posix())
            except ValueError as exc:
                errors.append(f"qualification support path invalid for {source_id}: {exc}")
        if asset_hash != correction.get("corrected_testbench_sha256"):
            errors.append(f"qualification corrected testbench hash is not bound: {source_id}")
        changed_location = row.get("changed_location")
        if not isinstance(changed_location, dict) or changed_location.get("file") not in {
            row.get("original_rtl_path"), row.get("mutated_rtl_path"), row.get("repaired_rtl_path"), row.get("testbench_path")
        }:
            errors.append(f"qualification changed location is undeclared: {source_id}")

    if workspace_files != declared_files:
        errors.append("qualification workspace contains undeclared or missing files")

    expected_cases = {
        (source_id, case.get("name"))
        for source_id, correction in correction_by_id.items()
        for case in (correction.get("negative_mutation_validation", {}).get("cases", []) if isinstance(correction.get("negative_mutation_validation"), dict) else [])
        if isinstance(case, dict)
    }
    actual_cases = {(row.get("source_id"), row.get("mutation_type")) for row in manifest_rows}
    if len(manifest_rows) != len(expected_cases) or len(actual_cases) != len(manifest_rows) or actual_cases != expected_cases:
        errors.append("qualification manifest does not cover exactly every recorded mutation contract")

    evidence_by_id = {row.get("mutation_id"): row for row in evidence_rows}
    if len(evidence_rows) != len(manifest_rows) or set(evidence_by_id) != set(manifest_by_mutation):
        errors.append("qualification evidence does not have exactly one row per mutation")
    for mutation_id, manifest_row in manifest_by_mutation.items():
        evidence = evidence_by_id.get(mutation_id)
        if evidence is None:
            continue
        source_id = manifest_row.get("source_id")
        if evidence.get("schema_version") != "rtl_mutation_evidence_v0.1":
            errors.append(f"qualification evidence schema mismatch: {mutation_id}")
        for field in ("mutation_id", "source_id", "top_module", "mutation_type", "mutated_signal", "changed_location", "requested_checks"):
            if evidence.get(field) != manifest_row.get(field):
                errors.append(f"qualification evidence identity mismatch: {mutation_id}")
                break
        checks = evidence.get("checks") if isinstance(evidence.get("checks"), dict) else {}
        compile_checks = checks.get("compile") if isinstance(checks.get("compile"), dict) else {}
        simulation_checks = checks.get("simulation") if isinstance(checks.get("simulation"), dict) else {}
        for name in ("original", "mutated", "repaired"):
            if not _status_passes(compile_checks.get(name)):
                errors.append(f"qualification compile did not pass: {mutation_id}:{name}")
        for name in ("original_passes", "mutated_detects_mutation", "repaired_passes"):
            if not _status_passes(simulation_checks.get(name)):
                errors.append(f"qualification simulation contract did not pass: {mutation_id}:{name}")
        if evidence.get("failure_category") != "passed":
            errors.append(f"qualification mutation was not detected as expected: {mutation_id}")
        for diagnostic_error in _qualification_diagnostic_errors(evidence.get("diagnostics")):
            errors.append(f"qualification evidence diagnostic invalid: {mutation_id}: {diagnostic_error}")
        hashes = evidence.get("input_hashes") if isinstance(evidence.get("input_hashes"), dict) else {}
        for field, manifest_field in (("original_sha256", "original_rtl_path"), ("mutated_sha256", "mutated_rtl_path"), ("repaired_sha256", "repaired_rtl_path"), ("testbench_sha256", "testbench_path")):
            try:
                actual_hash = _sha256_file(_safe_relative(workspace_root, manifest_row.get(manifest_field)))
            except ValueError:
                continue
            if hashes.get(field) != actual_hash:
                errors.append(f"qualification hash mismatch: {mutation_id}:{field}")
        private_reference_hash = (assets_by_id.get(source_id, {}).get("input_hashes") or {}).get("reference_rtl_sha256")
        if private_reference_hash and any(
            hashes.get(field) == private_reference_hash
            for field in ("original_sha256", "repaired_sha256")
        ):
            errors.append(f"qualification passing RTL collides with private reference hash: {source_id}")

    sidecar_ok = isinstance(sidecar, dict)
    if not sidecar_ok:
        errors.append("qualification runner sidecar is not an object")
    else:
        if sidecar.get("schema_version") != QUALIFICATION_SIDECAR_SCHEMA_VERSION:
            errors.append("qualification runner sidecar has the wrong schema")
        if sidecar.get("profile") != "pilot-docker" or sidecar.get("runtime") != "docker" or sidecar.get("runtime_mode") != "rootful-daemon" or sidecar.get("rootless") is not False:
            errors.append("qualification runner profile is not pilot-docker rootful-daemon")
        if expected_image_id is not None and sidecar.get("image_id") != expected_image_id:
            errors.append("qualification runner image identity mismatch")
        if sidecar.get("rtlbench_commit") != expected_rtlbench_commit:
            errors.append("qualification runner RTLBench commit mismatch")
        if sidecar.get("network_policy") != "none":
            errors.append("qualification runner network policy is not none")
        if sidecar.get("partial_evidence_sha256") is not None:
            errors.append("qualification runner references partial evidence")
        if sidecar.get("manifest_sha256") != _sha256_file(qualification_manifest_path):
            errors.append("qualification runner manifest hash mismatch")
        try:
            workspace_hash = _workspace_tree_sha256(workspace_root)
        except ValueError as exc:
            workspace_hash = None
            errors.append(str(exc))
        if workspace_hash is not None and sidecar.get("workspace_tree_sha256") != workspace_hash:
            errors.append("qualification runner workspace hash mismatch")
        if sidecar.get("evidence_sha256") != _sha256_file(qualification_evidence_path):
            errors.append("qualification runner evidence hash mismatch")

    rows_report = [
        {
            "source_id": source_id,
            "task_id": tasks_by_id.get(source_id, {}).get("task_id"),
            "correction_version": correction_by_id.get(source_id, {}).get("correction_version"),
            "corrected_testbench_sha256": (assets_by_id.get(source_id, {}).get("input_hashes") or {}).get("testbench_sha256"),
            "mutation_count": sum(1 for row in manifest_rows if row.get("source_id") == source_id),
            # A report with any global or row-scoped error is not a usable
            # qualification. Keep every row explicitly failed so a partial
            # report cannot be mistaken for a passed five-task gate.
            "qualification_passed": not errors,
        }
        for source_id in expected_ids
    ]
    report = {
        "schema_version": QUALIFICATION_REPORT_SCHEMA_VERSION,
        "source_commit": correction_report.get("source_commit"),
        "source_tree_sha256": correction_report.get("source_tree_sha256"),
        "frozen_split_sha256": correction_report.get("base_split_sha256"),
        "correction_version": CORRECTION_VERSION,
        "correction_manifest_sha256": _sha256_file(correction_manifest_path),
        "correction_report_sha256": _sha256_file(correction_report_path),
        "qualification_manifest_sha256": _sha256_file(qualification_manifest_path),
        "qualification_evidence_sha256": _sha256_file(qualification_evidence_path),
        "qualification_sidecar_sha256": _sha256_file(qualification_sidecar_path),
        "row_count": 5,
        "mutation_count": len(manifest_rows),
        "rows": rows_report,
        "runner": {
            "profile": sidecar.get("profile") if isinstance(sidecar, dict) else None,
            "image_id": sidecar.get("image_id") if isinstance(sidecar, dict) else None,
            "rtlbench_commit": sidecar.get("rtlbench_commit") if isinstance(sidecar, dict) else None,
        },
        "qualification_passed": not errors,
        "errors": sorted(set(errors)),
    }
    try:
        _write_exclusive_json(report_output, report)
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [str(exc)]}, 1
    return {**report, "ok": not errors}, 0 if not errors else 1


def validate_qualification_report_metadata(path: Path, expected_source_ids: Iterable[str] = EXPECTED_SOURCE_IDS) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        report = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {}, [str(exc)]
    if not isinstance(report, dict):
        return {}, ["qualification report must be a JSON object"]
    if report.get("schema_version") != QUALIFICATION_REPORT_SCHEMA_VERSION:
        errors.append("qualification report has the wrong schema")
    if report.get("qualification_passed") is not True:
        errors.append("qualification report is not passed")
    if report.get("correction_version") != CORRECTION_VERSION:
        errors.append("qualification report correction version mismatch")
    rows = report.get("rows")
    expected = list(expected_source_ids)
    if not isinstance(rows, list) or [row.get("source_id") for row in rows if isinstance(row, dict)] != expected:
        errors.append("qualification report rows do not match the expected source IDs")
    for index, row in enumerate(rows if isinstance(rows, list) else [], 1):
        if not isinstance(row, dict) or row.get("qualification_passed") is not True:
            errors.append(f"qualification report row {index} is not passed")
    return report, sorted(set(errors))


__all__ = [
    "CORRECTION_VERSION",
    "EXPECTED_SOURCE_IDS",
    "FROZEN_SPLIT_SHA256",
    "QUALIFICATION_REPORT_SCHEMA_VERSION",
    "QUALIFICATION_SIDECAR_SCHEMA_VERSION",
    "SOURCE_COMMIT",
    "SOURCE_TREE_SHA256",
    "SMOKE_RUN_SCHEMA_VERSION",
    "assemble_smoke_inputs",
    "prepare_normalization_smoke_run",
    "validate_asset_qualification",
    "validate_qualification_report_metadata",
    "validate_normalization_response",
    "validate_smoke_binding",
    "_workspace_tree_sha256",
]
