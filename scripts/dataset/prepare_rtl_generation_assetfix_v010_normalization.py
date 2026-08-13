"""Prepare a qualified assetfix_v010 pair for public normalization.

This is a metadata-only control-plane command.  It derives a compatibility
manifest from the immutable v010 correction manifest, binds the passed
qualification artifacts, and delegates public/private export to the existing
normalization exporter.  It never calls a model and never executes RTL.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.data_workspace_layout import initialize_manual_rtl_run
from scripts.dataset.rtl_generation_preparation import discover_source_rows, _task_id
from scripts.dataset.rtl_generation_qualified_subset import (
    prepare_qualified_normalization_run,
)


SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
CORRECTION_VERSION = "assetfix_v010"
CORRECTION_SCHEMA = "rtl_verification_asset_correction_row_v0.2"


class PreparationError(ValueError):
    """Raised when the qualified normalization boundary is not provable."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"required input is not a regular file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"invalid JSON input: {path}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"required JSONL input is not a regular file: {path}")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PreparationError(f"invalid JSONL row {path}:{number}") from exc
        if not isinstance(value, dict):
            raise PreparationError(f"JSONL row is not an object: {path}:{number}")
        rows.append(value)
    return rows


def _read_ids(path: Path, label: str) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise PreparationError(f"{label} is not a regular file")
    values = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != len(set(values)):
        raise PreparationError(f"{label} contains duplicate IDs")
    return values


def _write_exclusive(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise PreparationError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    content = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _write_jsonl_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists() or path.is_symlink():
        raise PreparationError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    content = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _secure_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise PreparationError("run root is not a regular directory")
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix(), reverse=True):
        if path.is_symlink():
            raise PreparationError(f"run tree contains a symlink: {path}")
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_dir():
            if mode != 0o700:
                os.chmod(path, 0o700)
        elif path.is_file():
            if mode != 0o600:
                os.chmod(path, 0o600)
        else:
            raise PreparationError(f"run tree contains a special file: {path}")
    os.chmod(root, 0o700)


def _compatibility_manifest(
    *,
    original_path: Path,
    selected_ids: list[str],
    inventory_path: Path,
    selection_ids_path: Path,
    selection_report_path: Path,
    qualification_report_path: Path,
    qualification_evidence_path: Path,
    runner_sidecar_path: Path,
    split_path: Path,
) -> list[dict[str, Any]]:
    original_rows = _load_jsonl(original_path)
    by_id = {row.get("source_id"): row for row in original_rows}
    if len(original_rows) != len(selected_ids) or [row.get("source_id") for row in original_rows] != selected_ids:
        raise PreparationError("v010 correction manifest does not match the pinned qualified order")
    inventory_rows = _load_jsonl(inventory_path)
    inventory = {row.get("source_id"): row for row in inventory_rows}
    qualification = _load_json(qualification_report_path)
    qualification_rows = {row.get("source_id"): row for row in qualification.get("rows", [])}
    selection_ids_sha256 = _sha256(selection_ids_path)
    selection_report_sha256 = _sha256(selection_report_path)
    split_sha256 = _sha256(split_path)
    report_sha256 = _sha256(qualification_report_path)
    evidence_sha256 = _sha256(qualification_evidence_path)
    sidecar_sha256 = _sha256(runner_sidecar_path)
    result: list[dict[str, Any]] = []
    for source_id in selected_ids:
        original = by_id[source_id]
        inventory_row = inventory.get(source_id)
        qualification_row = qualification_rows.get(source_id)
        if inventory_row is None or qualification_row is None:
            raise PreparationError(f"missing inventory or qualification row: {source_id}")
        if qualification_row.get("qualification_status") != "qualified":
            raise PreparationError(f"source is not qualified: {source_id}")
        if original.get("task_id") != inventory_row.get("task_id"):
            raise PreparationError(f"task identity mismatch: {source_id}")
        row = copy.deepcopy(original)
        row.update({
            "schema_version": CORRECTION_SCHEMA,
            "design_family": inventory_row.get("design_family"),
            "upstream_commit": SOURCE_COMMIT,
            "testbench_path": f"tasks/{source_id}/testbench.sv",
            "frozen_split_sha256": split_sha256,
            "source_tree_sha256": SOURCE_TREE_SHA256,
            "public_specification_sha256": inventory_row.get("source_prompt_sha256"),
            "selection_ids_sha256": selection_ids_sha256,
            "selection_report_sha256": selection_report_sha256,
            "qualification_evidence_sha256": evidence_sha256,
            "qualification_report_sha256": report_sha256,
            "qualification_result": "passed",
            "qualification_runner_sidecar_sha256": sidecar_sha256,
            "static_audit": {
                "dependency_closure": "passed",
                "reference_rtl_supplied": False,
                "support_file_count": 0,
            },
            # The source overlay is pending subset materialization; the
            # qualification result above records that the immutable evidence
            # has already passed.
            "qualification_status": "pending_isolated_qualification",
            "verification_readiness": "pending_qualification",
        })
        result.append(row)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--split", required=True, type=Path)
    parser.add_argument("--selection-ids", required=True, type=Path)
    parser.add_argument("--selection-report", required=True, type=Path)
    parser.add_argument("--qualification-report", required=True, type=Path)
    parser.add_argument("--qualification-evidence", required=True, type=Path)
    parser.add_argument("--runner-evidence", required=True, type=Path)
    parser.add_argument("--runner-sidecar", required=True, type=Path)
    parser.add_argument("--qualified-task-ids", required=True, type=Path)
    parser.add_argument("--failed-task-ids", required=True, type=Path)
    parser.add_argument("--correction-manifest", required=True, type=Path)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result: dict[str, Any]
    code = 1
    try:
        selected_ids = _read_ids(args.selection_ids, "selection IDs")
        qualified_ids = _read_ids(args.qualified_task_ids, "qualified task IDs")
        failed_ids = _read_ids(args.failed_task_ids, "failed task IDs")
        if selected_ids != qualified_ids or failed_ids:
            raise PreparationError("qualified-two normalization requires all selected IDs and no failed IDs")
        if _sha256(args.split) != SPLIT_SHA256:
            raise PreparationError("frozen split hash mismatch")
        if _sha256(args.inventory) != INVENTORY_SHA256:
            raise PreparationError("inventory hash mismatch")
        if _sha256(args.selection_report) != "b50f41322c5086707fa26fe94844563621c2a647c899cb45453ef5e6df407431":
            raise PreparationError("selection report hash mismatch")
        qualification = _load_json(args.qualification_report)
        if qualification.get("qualification_passed") is not True or qualification.get("qualified_tasks") != len(selected_ids):
            raise PreparationError("qualification report does not prove the selected pair passed")
        if qualification.get("source_commit") != SOURCE_COMMIT or qualification.get("source_tree_sha256") != SOURCE_TREE_SHA256:
            raise PreparationError("qualification source attestation mismatch")
        if qualification.get("reference_rtl_supplied") is not False or qualification.get("support_file_count") != 0:
            raise PreparationError("qualification privacy contract mismatch")
        rows, discovery_errors = discover_source_rows(args.input)
        if discovery_errors:
            raise PreparationError("source discovery failed: " + "; ".join(discovery_errors))
        discovered = {row.source_id: row for row in rows}
        if any(source_id not in discovered for source_id in selected_ids):
            raise PreparationError("selected source is absent from the pinned source view")
        for source_id in selected_ids:
            source = discovered[source_id]
            source.source_commit = SOURCE_COMMIT
            source.provenance["source_commit"] = SOURCE_COMMIT
            if _task_id(source) != next(row.get("task_id") for row in _load_jsonl(args.correction_manifest) if row.get("source_id") == source_id):
                raise PreparationError(f"source task identity mismatch: {source_id}")

        initialized = initialize_manual_rtl_run(args.run_id, "VerilogEval", args.runs_root)
        run_root = args.runs_root / args.run_id
        compatibility_path = run_root / "reports" / "qualified_correction_manifest_input.jsonl"
        compatibility_rows = _compatibility_manifest(
            original_path=args.correction_manifest,
            selected_ids=selected_ids,
            inventory_path=args.inventory,
            selection_ids_path=args.selection_ids,
            selection_report_path=args.selection_report,
            qualification_report_path=args.qualification_report,
            qualification_evidence_path=args.qualification_evidence,
            runner_sidecar_path=args.runner_sidecar,
            split_path=args.split,
        )
        _write_jsonl_exclusive(compatibility_path, compatibility_rows)
        output_hashes_path = run_root / "reports" / "qualification_output_hashes.json"
        failed_hash = _sha256(args.failed_task_ids)
        report_hash = _sha256(args.qualification_report)
        raw_evidence_hash = _sha256(args.runner_evidence)
        output_hashes = {
            "schema_version": "rtl_generation_qualification_output_hashes_v0.2",
            "asset_manifest_sha256": _sha256(args.correction_manifest),
            "evidence_sha256": raw_evidence_hash,
            "raw_candidate_evidence_sha256": raw_evidence_hash,
            "runner_evidence_sha256": raw_evidence_hash,
            "qualification_evidence_sha256": _sha256(args.qualification_evidence),
            "qualification_report_sha256": report_hash,
            "qualification_validation_report_sha256": report_hash,
            "runner_sidecar_sha256": _sha256(args.runner_sidecar),
            "qualified_task_ids_sha256": _sha256(args.qualified_task_ids),
            "failed_or_inconclusive_task_ids_sha256": failed_hash,
            "failed_task_ids_sha256": failed_hash,
            "selection_ids_sha256": _sha256(args.selection_ids),
            "split_sha256": _sha256(args.split),
        }
        _write_exclusive(output_hashes_path, output_hashes)
        prepared, prepare_code = prepare_qualified_normalization_run(
            source_input=args.input,
            source_root=args.source_root,
            inventory_path=args.inventory,
            split_path=args.split,
            selection_ids_path=args.selection_ids,
            qualification_report_path=args.qualification_report,
            qualification_output_hashes_path=output_hashes_path,
            qualified_task_ids_path=args.qualified_task_ids,
            failed_task_ids_path=args.failed_task_ids,
            correction_manifest_path=compatibility_path,
            correction_root=args.correction_root,
            run_root=run_root,
            expected_source_commit=SOURCE_COMMIT,
            expected_source_tree_sha256=SOURCE_TREE_SHA256,
            expected_inventory_sha256=INVENTORY_SHA256,
            expected_split_sha256=SPLIT_SHA256,
            expected_correction_version=CORRECTION_VERSION,
            source_correction_manifest_sha256=_sha256(args.correction_manifest),
            source_selection_report_sha256=_sha256(args.selection_report),
        )
        if prepare_code:
            result = {"initialized": initialized, **prepared}
            code = prepare_code
        else:
            _secure_tree(run_root)
            result = {"initialized": initialized, **prepared}
            code = 0
    except (OSError, UnicodeError, json.JSONDecodeError, PreparationError, ValueError, StopIteration) as exc:
        result = {"ok": False, "stage": "qualified_v010_normalization", "errors": [str(exc)]}
        code = 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"ok={result.get('ok', False)}")
        for error in result.get("errors", []):
            print(f"error: {error}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
