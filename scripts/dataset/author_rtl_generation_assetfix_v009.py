#!/usr/bin/env python3
"""Author the controlled-reprocessing assetfix_v009 overlay.

This command uses only public-specification-derived catalogs already present
in the repository.  It never reads reference RTL or upstream testbench
content and never invokes an HDL tool, compiler, simulator, RTLBench, Docker,
or an external model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset import author_rtl_generation_assetfix_v005 as v005
from scripts.dataset import author_rtl_generation_assetfix_v006 as v006
from scripts.dataset import author_rtl_generation_assetfix_v008 as v008
from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    static_testbench_audit,
)


CORRECTION_VERSION = "assetfix_v009"
SOURCE_COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
SOURCE_TREE_SHA256 = "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
INVENTORY_SHA256 = "fe20a05b9041a194b1553bf391dcfa2e005f6f9d9fd1f59d134e6d0811eceaed"
SPLIT_SHA256 = "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
AUTHORING_SCHEMA_VERSION = "rtl_asset_qualification_authoring_row_v0.1"

CATALOG_MODULES = (v005, v006, v008)
PUBLIC_TESTBENCHES: dict[str, str] = {}
PUBLIC_FIXTURES: dict[str, dict[str, str]] = {}
for module in CATALOG_MODULES:
    PUBLIC_TESTBENCHES.update(module.PUBLIC_TESTBENCHES)
    PUBLIC_FIXTURES.update(module.PUBLIC_FIXTURES)

PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    ".local_data",
    "reference.sv",
    "_ref.sv",
    "refmodule",
    "candidate_evidence",
)
FIXTURE_FORBIDDEN_MARKERS = PRIVATE_MARKERS + (
    "testbench",
    "mismatches:",
    "$finish",
    "$display",
    "`include",
    "package ",
    "interface ",
)
MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)\b", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def _validate_fixture(source_id: str, name: str, content: str) -> list[str]:
    lowered = content.casefold()
    errors: list[str] = []
    for marker in FIXTURE_FORBIDDEN_MARKERS:
        if marker.casefold() in lowered:
            errors.append(f"{source_id}:{name}: forbidden marker {marker}")
    modules = MODULE_RE.findall(content)
    if modules != ["TopModule"]:
        errors.append(f"{source_id}:{name}: fixture must declare only TopModule")
    if content.count("TopModule") < 1:
        errors.append(f"{source_id}:{name}: TopModule declaration missing")
    return errors


def _validate_inputs(
    *,
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
    metadata_path: Path,
    authorization_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    inventory_rows = load_jsonl(inventory_path)
    inventory = {row["source_id"]: row for row in inventory_rows}
    selection = load_json(selection_path)
    metadata = load_json(metadata_path)
    authorization = load_json(authorization_path)
    selected = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    if len(selected) != 20 or len(set(selected)) != 20:
        raise ValueError("assetfix_v009 requires exactly 20 unique selected IDs")
    if selection.get("ok") is not True:
        raise ValueError("selection report is not successful")
    if selection.get("correction_version") != CORRECTION_VERSION:
        raise ValueError("selection correction version mismatch")
    if selection.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("selection source commit mismatch")
    if selection.get("source_tree_sha256") != SOURCE_TREE_SHA256:
        raise ValueError("selection source tree hash mismatch")
    if selection.get("base_inventory_sha256") != INVENTORY_SHA256:
        raise ValueError("selection inventory hash mismatch")
    if selection.get("base_split_sha256") != SPLIT_SHA256:
        raise ValueError("selection split hash mismatch")
    if selection.get("split") != "train":
        raise ValueError("selection is not train-only")
    if [row.get("source_id") for row in selection.get("rows", [])] != selected:
        raise ValueError("selection order mismatch")
    if selection.get("selection_ids_sha256") != sha256_file(ids_path):
        raise ValueError("selection ID hash mismatch")
    if selection.get("selection_metadata_sha256") != sha256_file(metadata_path):
        raise ValueError("selection metadata hash mismatch")
    if metadata.get("lineage_policy") != "controlled_reprocessing_of_train_tasks_not_in_frozen_accepted_packages":
        raise ValueError("reprocessing lineage policy mismatch")
    if authorization.get("status") != "selection_authorized":
        raise ValueError("reprocessing authorization is not active")
    if authorization.get("selection_report_sha256") != sha256_file(selection_path):
        raise ValueError("authorization selection-report binding mismatch")
    if authorization.get("selection_ids_sha256") != sha256_file(ids_path):
        raise ValueError("authorization selection-ID binding mismatch")
    if authorization.get("selected_source_ids") != selected:
        raise ValueError("authorization order mismatch")

    for source_id in selected:
        source = inventory.get(source_id)
        if source is None:
            raise ValueError(f"source ID missing from inventory: {source_id}")
        if source.get("verification_readiness") != "needs_testbench":
            raise ValueError(f"source is not needs_testbench: {source_id}")
        if source.get("interface_deterministic") is not True:
            raise ValueError(f"source interface is not deterministic: {source_id}")
        if source_id not in PUBLIC_TESTBENCHES or source_id not in PUBLIC_FIXTURES:
            raise ValueError(f"public authoring catalog is incomplete: {source_id}")
        expected_names = ("positive", *MUTATION_NAMES[source_id])
        if tuple(PUBLIC_FIXTURES[source_id]) != expected_names:
            raise ValueError(f"fixture order mismatch: {source_id}")
        _, audit_errors = static_testbench_audit(PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        if audit_errors:
            raise ValueError(f"testbench static audit failed for {source_id}: {audit_errors}")
        for name, content in PUBLIC_FIXTURES[source_id].items():
            errors = _validate_fixture(source_id, name, content)
            if errors:
                raise ValueError("; ".join(errors))
    return inventory, selection, selected


def author(
    *,
    correction_root: Path,
    inventory_path: Path,
    selection_path: Path,
    ids_path: Path,
    metadata_path: Path,
    authorization_path: Path,
) -> dict[str, Any]:
    if correction_root.exists() or correction_root.is_symlink():
        raise ValueError(f"refusing to replace existing overlay: {correction_root}")
    inventory, selection, selected = _validate_inputs(
        inventory_path=inventory_path,
        selection_path=selection_path,
        ids_path=ids_path,
        metadata_path=metadata_path,
        authorization_path=authorization_path,
    )
    correction_root.mkdir(mode=0o700, parents=True)
    os.chmod(correction_root, 0o700)
    tasks_root = correction_root / "tasks"
    qualification_root = correction_root / "qualification"
    tasks_root.mkdir(mode=0o700)
    qualification_root.mkdir(mode=0o700)
    authoring_rows: list[dict[str, Any]] = []
    testbench_hashes: dict[str, str] = {}
    fixture_hashes: dict[str, dict[str, str]] = {}
    selection_rows = {row["source_id"]: row for row in selection["rows"]}

    for source_id in selected:
        testbench_path = tasks_root / source_id / "testbench.sv"
        testbench_bytes = PUBLIC_TESTBENCHES[source_id].encode("utf-8")
        write_exclusive(testbench_path, testbench_bytes)
        testbench_hashes[source_id] = sha256_bytes(testbench_bytes)
        fixture_hashes[source_id] = {}
        for name, content in PUBLIC_FIXTURES[source_id].items():
            fixture_path = qualification_root / source_id / f"{name}.sv"
            fixture_bytes = content.encode("utf-8")
            write_exclusive(fixture_path, fixture_bytes)
            fixture_hashes[source_id][name] = sha256_bytes(fixture_bytes)
        authoring_rows.append({
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "source_id": source_id,
            "task_id": inventory[source_id]["task_id"],
            "top_module": "TopModule",
            "positive_rtl_path": f"{source_id}/positive.sv",
            "negative_mutations": [
                {
                    "name": name,
                    "rtl_path": f"{source_id}/{name}.sv",
                    "authoring_method": "trusted_manual_public_spec_mutation",
                    "oracle_basis": "public_specification_only",
                    "expected_outcome": "rejected",
                }
                for name in MUTATION_NAMES[source_id]
            ],
            "reference_used": False,
            "support_files": [],
            "lineage": "controlled_reprocessing",
            "prior_assetfix_versions": ["assetfix_v005", "assetfix_v006", "assetfix_v008"],
            "selection_role": selection_rows[source_id].get("selection_role"),
        })

    authoring_manifest = qualification_root / "authoring_manifest.jsonl"
    write_exclusive(
        authoring_manifest,
        b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in authoring_rows),
    )
    attestation = {
        "schema_version": "rtl_verification_asset_correction_authoring_v0.1",
        "correction_version": CORRECTION_VERSION,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "inventory_sha256": INVENTORY_SHA256,
        "frozen_split_sha256": SPLIT_SHA256,
        "selection_ids_sha256": sha256_file(ids_path),
        "selection_report_sha256": sha256_file(selection_path),
        "selection_metadata_sha256": sha256_file(metadata_path),
        "reprocessing_authorization_sha256": sha256_file(authorization_path),
        "selected_source_ids": selected,
        "selected_count": len(selected),
        "public_specification_hashes": {
            source_id: inventory[source_id]["source_prompt_sha256"] for source_id in selected
        },
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
        "testbench_count": len(selected),
        "positive_case_count": len(selected),
        "negative_case_count": sum(len(MUTATION_NAMES[source_id]) for source_id in selected),
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "authoring_method": "trusted_manual_public_spec",
        "lineage": "controlled_reprocessing",
        "correction_reason": "bounded reprocessing overlay for tasks absent from frozen accepted packages",
        "dependency_closure": "pending_static_validation",
        "qualification_status": "pending_isolated_qualification",
        "errors": [],
    }
    attestation_path = correction_root / "authoring_attestation.json"
    write_exclusive(attestation_path, (json.dumps(attestation, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {
        "ok": True,
        "correction_root": correction_root.as_posix(),
        "authoring_manifest": authoring_manifest.as_posix(),
        "authoring_attestation": attestation_path.as_posix(),
        "selected_source_ids": selected,
        "selected_count": len(selected),
        "testbench_count": len(selected),
        "positive_case_count": len(selected),
        "negative_case_count": sum(len(MUTATION_NAMES[source_id]) for source_id in selected),
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "selection_ids_sha256": sha256_file(ids_path),
        "selection_report_sha256": sha256_file(selection_path),
        "selection_metadata_sha256": sha256_file(metadata_path),
        "authorization_sha256": sha256_file(authorization_path),
        "testbench_hashes": testbench_hashes,
        "fixture_hashes": fixture_hashes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correction-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--ids", required=True, type=Path)
    parser.add_argument("--selection-metadata", required=True, type=Path)
    parser.add_argument("--authorization", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = author(
            correction_root=args.correction_root,
            inventory_path=args.inventory,
            selection_path=args.selection,
            ids_path=args.ids,
            metadata_path=args.selection_metadata,
            authorization_path=args.authorization,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
