from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.rtl_generation_inventory import (
    INVENTORY_SCHEMA_VERSION,
    audit_source_inventory,
    freeze_generation_split,
    load_split_source_ids,
    validate_generation_split,
    write_inventory_outputs,
)
from scripts.dataset.select_rtl_generation_smoke import select_smoke_rows
from scripts.dataset.rtl_generation_preparation import export_generation_normalization_batches
from scripts.dataset.rtl_generation_preparation import _sv_probable_instantiations, _sv_tokens
from tests.dataset.rtl_generation_test_helpers import make_checkout


COMMIT = "a" * 40


def test_dependency_scanner_does_not_misclassify_procedural_delay_as_parameterization() -> None:
    tokens, token_ambiguous = _sv_tokens(
        "module tb; initial begin clk = 0; #5 clk = ~clk; end endmodule"
    )
    instances, instance_ambiguous = _sv_probable_instantiations(tokens)
    assert token_ambiguous is False
    assert instance_ambiguous is False
    assert instances == []


def test_complete_inventory_is_metadata_only_and_attested(tmp_path) -> None:
    source_root = tmp_path / "checkout"
    source = make_checkout(tmp_path, rows=3)
    source_root = source.parent
    report, rows, acquisition, code = audit_source_inventory(source, source_root, COMMIT)

    assert code == 0, report
    assert len(rows) == 3
    assert report["total_rows"] == 3
    assert acquisition["source_commit"] == COMMIT
    assert acquisition["source_tree_sha256"]
    assert acquisition["dirty_checkout"] is None
    assert all(row["schema_version"] == INVENTORY_SCHEMA_VERSION for row in rows)
    serialized = json.dumps(rows) + json.dumps(acquisition)
    assert "module RefModule" not in serialized
    assert "synthetic checker text" not in serialized
    assert all(row["task_id"].startswith("rtlgen_") for row in rows)
    assert all(row["verification_readiness"] == "executable_ready" for row in rows)


def test_inventory_rejects_duplicate_source_ids(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    source = root / "rows.jsonl"
    row = {
        "source_id": "same",
        "source_dataset": "VerilogEval",
        "license": "MIT",
        "prompt": "Implement module named TopModule.",
        "artifacts": {"rtl_code": "module Ref; endmodule", "testbench": "module tb; endmodule"},
        "provenance": {"public_dataset_name": "VerilogEval"},
    }
    source.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    report, rows, _, code = audit_source_inventory(source, root, COMMIT)
    assert code == 1
    assert len(rows) == 2
    assert any("duplicate source_id" in error for error in report["errors"])


def test_frozen_split_is_deterministic_and_family_isolated(tmp_path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    rows = []
    for index, family in enumerate(("comb", "comb", "seq", "seq", "fsm", "fsm"), 1):
        rows.append({
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "source_id": f"Prob{index:03d}",
            "source_dataset": "VerilogEval",
            "source_commit": COMMIT,
            "license": "MIT",
            "design_family": family,
            "task_id": f"task_{index}",
            "source_prompt_sha256": "1" * 64,
            "reference_rtl_sha256": "2" * 64,
            "testbench_sha256": "3" * 64,
            "support_file_hashes": [],
            "top_module": "TopModule",
            "interface_deterministic": True,
            "verification_readiness": "executable_ready",
            "readiness_reasons": [],
            "duplicate_group": None,
            "missing_files": [],
            "source_path_identity": f"dataset/Prob{index:03d}",
            "unresolved_dependency_count": 0,
            "unresolved_package_count": 0,
            "dependency_analysis_ambiguous": False,
            "testbench_top_module_count": 1,
            "warning_codes": [],
        })
    inventory.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    first = tmp_path / "split-one.json"
    second = tmp_path / "split-two.json"
    result, code = freeze_generation_split(inventory, first)
    assert code == 0, result
    result, code = freeze_generation_split(inventory, second)
    assert code == 0, result
    assert first.read_bytes() == second.read_bytes()
    report, code = validate_generation_split(inventory, first, expected_seed=7)
    assert code == 0, report
    manifest = json.loads(first.read_text(encoding="utf-8"))
    family_by_split = {
        name: {row["design_family"] for row in manifest["rows"] if row["split"] == name}
        for name in ("train", "validation", "test")
    }
    assert not family_by_split["train"] & family_by_split["validation"]
    assert not family_by_split["train"] & family_by_split["test"]
    assert not family_by_split["validation"] & family_by_split["test"]


def test_allowlist_must_stay_inside_requested_split(tmp_path) -> None:
    inventory = tmp_path / "inventory.jsonl"
    row = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_id": "one",
        "source_dataset": "VerilogEval",
        "source_commit": COMMIT,
        "license": "MIT",
        "design_family": "comb",
        "task_id": "task_one",
        "source_prompt_sha256": "1" * 64,
        "reference_rtl_sha256": "2" * 64,
        "testbench_sha256": "3" * 64,
        "support_file_hashes": [],
        "top_module": "TopModule",
        "interface_deterministic": True,
        "verification_readiness": "executable_ready",
        "readiness_reasons": [],
        "duplicate_group": None,
        "missing_files": [],
        "source_path_identity": "dataset/one",
        "unresolved_dependency_count": 0,
        "unresolved_package_count": 0,
        "dependency_analysis_ambiguous": False,
        "testbench_top_module_count": 1,
        "warning_codes": [],
    }
    inventory.write_text(json.dumps(row) + "\n", encoding="utf-8")
    split = tmp_path / "split.json"
    result, code = freeze_generation_split(inventory, split)
    assert code == 0, result
    allowlist = tmp_path / "ids.txt"
    allowlist.write_text("not-in-split\n", encoding="utf-8")
    selected, errors = load_split_source_ids(split, split="train", inventory_path=inventory, source_ids_path=allowlist)
    assert selected == ["not-in-split"]
    assert any("outside split" in error for error in errors)


def test_split_aware_normalization_export_honors_exact_allowlist(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=3)
    source_root = source.parent
    report, rows, acquisition, code = audit_source_inventory(source, source_root, COMMIT)
    assert code == 0, report
    inventory = tmp_path / "inventory.jsonl"
    acquisition_path = tmp_path / "acquisition.json"
    write_inventory_outputs(report, rows, acquisition, inventory, acquisition_path)
    split = tmp_path / "split.json"
    result, code = freeze_generation_split(inventory, split)
    assert code == 0, result
    manifest = json.loads(split.read_text(encoding="utf-8"))
    selected_id = manifest["splits"]["train"][0]
    allowlist = tmp_path / "train_ids.txt"
    allowlist.write_text(selected_id + "\n", encoding="utf-8")
    selected, errors = load_split_source_ids(split, split="train", inventory_path=inventory, source_ids_path=allowlist)
    assert errors == []
    public = tmp_path / "public"
    private = tmp_path / "private"
    result, code = export_generation_normalization_batches(
        source,
        public,
        private,
        batch_size=5,
        source_commit=COMMIT,
        source_ids=selected,
    )
    assert code == 0, result
    assert result["exported_rows"] == 1
    batch = json.loads((public / "batch_001.json").read_text(encoding="utf-8"))
    assert batch["rows"][0]["source_id"] == selected_id


def test_inventory_writer_can_preserve_the_audit_report(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=2)
    report, rows, acquisition, code = audit_source_inventory(source, source.parent, COMMIT)
    assert code == 0, report
    inventory = tmp_path / "inventory.jsonl"
    acquisition_path = tmp_path / "acquisition.json"
    audit_path = tmp_path / "audit.json"
    write_inventory_outputs(
        report,
        rows,
        acquisition,
        inventory,
        acquisition_path,
        audit_path=audit_path,
    )
    assert json.loads(audit_path.read_text(encoding="utf-8")) == report


def test_inventory_cli_writes_complete_control_artifacts(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=2)
    root = Path(__file__).resolve().parents[2]
    inventory = tmp_path / "inventory.jsonl"
    acquisition = tmp_path / "acquisition.json"
    command = [
        sys.executable,
        str(root / "scripts/dataset/audit_rtl_generation_sources.py"),
        "--input", str(source),
        "--source-root", str(source.parent),
        "--source-commit", COMMIT,
        "--output-json", str(tmp_path / "audit.json"),
        "--output-markdown", str(tmp_path / "audit.md"),
        "--inventory-output", str(inventory),
        "--acquisition-output", str(acquisition),
        "--json",
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len([line for line in inventory.read_text(encoding="utf-8").splitlines() if line]) == 2
    assert json.loads(acquisition.read_text(encoding="utf-8"))["source_commit"] == COMMIT
    assert (tmp_path / "inventory_missing.json").is_file()
    assert (tmp_path / "inventory_duplicates.json").is_file()
    assert (tmp_path / "inventory_license.json").is_file()


def test_smoke_selector_is_train_only_executable_ready_and_hash_bound(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=6)
    report, rows, acquisition, code = audit_source_inventory(source, source.parent, COMMIT)
    assert code == 0, report
    inventory = tmp_path / "inventory.jsonl"
    acquisition_path = tmp_path / "acquisition.json"
    write_inventory_outputs(report, rows, acquisition, inventory, acquisition_path)
    split = tmp_path / "split.json"
    split_result, split_code = freeze_generation_split(inventory, split)
    assert split_code == 0, split_result
    ids = tmp_path / "smoke_ids.txt"
    smoke_report = tmp_path / "smoke.json"
    result, smoke_code = select_smoke_rows(inventory, split, ids, smoke_report)
    assert smoke_code == 0, result
    assert result["selected_count"] == 5
    selected = [line for line in ids.read_text(encoding="utf-8").splitlines() if line]
    assert len(selected) == 5
    split_manifest = json.loads(split.read_text(encoding="utf-8"))
    assert set(selected).issubset(set(split_manifest["splits"]["train"]))
    assert json.loads(smoke_report.read_text(encoding="utf-8"))["inventory_sha256"] == hashlib.sha256(inventory.read_bytes()).hexdigest()
