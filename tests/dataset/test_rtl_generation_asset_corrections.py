from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.dataset.rtl_generation_asset_corrections import (
    CORRECTION_ROW_SCHEMA_VERSION,
    V003_ROW_SCHEMA_VERSION,
    _mutation_contract,
    _structural_errors,
    overlay_source_rows,
    select_correction_rows,
)
from scripts.dataset.rtl_generation_inventory import INVENTORY_SCHEMA_VERSION
from scripts.dataset.rtl_generation_preparation import SourceRow, export_generation_normalization_batches
from tests.dataset.rtl_generation_test_helpers import make_checkout


COMMIT = "a" * 40


def _inventory_row(source_id: str, family: str = "rtl") -> dict:
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_id": source_id,
        "source_dataset": "VerilogEval",
        "source_commit": COMMIT,
        "license": "MIT",
        "design_family": family,
        "task_id": f"task_{source_id}",
        "source_prompt_sha256": "1" * 64,
        "reference_rtl_sha256": "2" * 64,
        "testbench_sha256": "3" * 64,
        "support_file_hashes": [],
        "top_module": "TopModule",
        "interface_deterministic": True,
        "clock_signal_count": 0,
        "reset_signal_count": 0,
        "behavior_categories": ["combinational"],
        "verification_readiness": "needs_testbench",
        "readiness_reasons": [
            "testbench depends on a non-candidate module that is available only in private reference material",
        ],
        "duplicate_group": None,
        "missing_files": [],
        "source_path_identity": f"dataset/{source_id}",
        "unresolved_dependency_count": 1,
        "unresolved_package_count": 0,
        "dependency_analysis_ambiguous": False,
        "testbench_top_module_count": 1,
        "warning_codes": [],
    }


def _write_inventory_and_split(tmp_path: Path, ids: list[str]) -> tuple[Path, Path]:
    inventory = tmp_path / "inventory.jsonl"
    inventory.write_text(
        "".join(json.dumps(_inventory_row(source_id), sort_keys=True) + "\n" for source_id in ids),
        encoding="utf-8",
    )
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps(
            {
                "schema_version": "rtl_generation_split_v0.1",
                "inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
                "inventory_path": str(inventory),
                "algorithm": "family_isolated_random_v1",
                "seed": 7,
                "ratios": {"train": 1.0, "validation": 0.0, "test": 0.0},
                "row_count": len(ids),
                "eligible_generation_policy": {},
                "eligible_generation_pool": {"source_ids": {"train": [], "validation": [], "test": []}, "counts": {"train": 0, "validation": 0, "test": 0}, "ratios": {"train": 0.0, "validation": 0.0, "test": 0.0}, "total": 0},
                "splits": {"train": ids, "validation": [], "test": []},
                "rows": [{"source_id": source_id, "task_id": f"task_{source_id}", "design_family": "rtl", "verification_readiness": "needs_testbench", "split": "train"} for source_id in ids],
                "counts": {"train": len(ids), "validation": 0, "test": 0},
                "errors": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return inventory, split


def test_correction_selection_is_train_only_and_hash_bound(tmp_path: Path) -> None:
    ids = [f"Prob{i:03d}" for i in range(1, 6)]
    inventory, split = _write_inventory_and_split(tmp_path, ids)
    output = tmp_path / "selection.json"
    report, code = select_correction_rows(inventory, split, output, source_ids=ids)
    assert code == 0, report
    assert report["selected_count"] == 5
    assert json.loads(output.read_text(encoding="utf-8"))["base_inventory_sha256"] == hashlib.sha256(inventory.read_bytes()).hexdigest()


def test_correction_structural_gate_rejects_reference_dependency() -> None:
    row = SourceRow(
        source_id="Prob001_zero",
        source_dataset="VerilogEval",
        design_family="rtl",
        specification="Build a circuit that always outputs a LOW.",
        reference_rtl="module RefModule; endmodule\n",
        testbench=None,
        license="MIT",
        top_module_hint="TopModule",
        interface_hints=[{"name": "zero", "direction": "output"}],
    )
    valid = """module tb;
wire zero;
TopModule dut (.zero(zero));
initial begin
  $display(\"Mismatches: %0d\", 0);
  $finish;
end
endmodule
"""
    assert _structural_errors(row, valid) == []
    assert _structural_errors(row, valid.replace("TopModule", "RefModule"))


def test_correction_structural_gate_rejects_legacy_result_label() -> None:
    row = SourceRow(
        source_id="Prob001_zero",
        source_dataset="VerilogEval",
        design_family="rtl",
        specification="Build a circuit that always outputs a LOW.",
        reference_rtl="module RefModule; endmodule\n",
        testbench=None,
        license="MIT",
        top_module_hint="TopModule",
        interface_hints=[{"name": "zero", "direction": "output"}],
    )
    legacy = """module tb;
wire zero;
TopModule dut (.zero(zero));
initial begin
  $display(\"mismatch_count_v1: %0d\", 0);
  $finish;
end
endmodule
"""
    errors = _structural_errors(row, legacy)
    assert any("canonical Mismatches" in error for error in errors)
    assert any("legacy mismatch_count_v1" in error for error in errors)


def test_offline_mutation_contracts_have_distinguishing_witnesses() -> None:
    for source_id in (
        "Prob001_zero",
        "Prob020_mt2015_eq2",
        "Prob071_always_casez",
        "Prob048_m2014_q4c",
        "Prob079_fsm3onehot",
    ):
        contract = _mutation_contract(source_id)
        assert contract["validated_offline"] is True
        assert contract["execution_deferred"] is True
        assert all(item["distinguishing_case_count"] > 0 for item in contract["cases"])


def test_overlay_replaces_only_the_private_testbench(tmp_path: Path) -> None:
    source = make_checkout(tmp_path, rows=1)
    from scripts.dataset.rtl_generation_preparation import discover_source_rows, _sha256, _text_bytes, _task_id

    rows, errors = discover_source_rows(source)
    assert errors == []
    row = rows[0]
    row.source_commit = COMMIT
    row.provenance["source_commit"] = COMMIT
    corrected = """module tb;
wire clk;
wire d;
wire q;
TopModule dut (.clk(clk), .d(d), .q(q));
initial begin
  $display(\"Mismatches: %0d\", 0);
  $finish;
end
endmodule
"""
    root = tmp_path / "correction"
    path = root / "tasks" / row.source_id / "testbench.sv"
    path.parent.mkdir(parents=True)
    path.write_text(corrected, encoding="utf-8")
    manifest = root / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": CORRECTION_ROW_SCHEMA_VERSION,
                "source_dataset": row.source_dataset,
                "source_id": row.source_id,
                "task_id": _task_id(row),
                "split": "train",
                "design_family": row.design_family,
                "top_module": "TopModule",
                "upstream_commit": COMMIT,
                "original_prompt_sha256": _sha256(_text_bytes(row.specification)),
                "original_reference_rtl_sha256": _sha256(_text_bytes(row.reference_rtl)),
                "original_testbench_sha256": _sha256(_text_bytes(row.testbench)),
                "corrected_testbench_sha256": _sha256(corrected),
                "correction_version": "assetfix_v002",
                "correction_reason": "test",
                "authoring_method": "trusted_manual_public_spec",
                "reference_modified": False,
                "reference_copied_to_support": False,
                "testbench_path": f"tasks/{row.source_id}/testbench.sv",
                "support_files": [],
                "dependency_closure": "passed",
                "verification_readiness": "executable_ready",
                "negative_mutation_validation": _mutation_contract("Prob001_zero"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    overlay, overlay_errors, correction_rows = overlay_source_rows(rows, manifest, root)
    assert overlay_errors == []
    assert set(correction_rows) == {row.source_id}
    assert overlay[0].testbench == corrected
    assert overlay[0].specification == row.specification
    assert overlay[0].reference_rtl == row.reference_rtl


@pytest.mark.parametrize(
    "correction_version",
    ["assetfix_v006_retry_02", "assetfix_v008", "assetfix_v008_retry_01"],
)
def test_overlay_accepts_extended_manifest_versions(
    tmp_path: Path,
    correction_version: str,
) -> None:
    source = make_checkout(tmp_path, rows=1)
    from scripts.dataset.rtl_generation_preparation import discover_source_rows, _sha256, _text_bytes, _task_id

    rows, errors = discover_source_rows(source)
    assert errors == []
    row = rows[0]
    row.source_commit = COMMIT
    row.provenance["source_commit"] = COMMIT
    corrected = """module tb;
wire zero;
TopModule dut (.zero(zero));
initial begin
  $display(\"Mismatches: %0d\", 0);
  $finish;
end
endmodule
"""
    root = tmp_path / "correction"
    path = root / "tasks" / row.source_id / "testbench.sv"
    path.parent.mkdir(parents=True)
    path.write_text(corrected, encoding="utf-8")
    manifest = root / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": V003_ROW_SCHEMA_VERSION,
                "source_dataset": row.source_dataset,
                "source_id": row.source_id,
                "task_id": _task_id(row),
                "split": "train",
                "design_family": row.design_family,
                "top_module": "TopModule",
                "upstream_commit": COMMIT,
                "original_prompt_sha256": _sha256(_text_bytes(row.specification)),
                "original_reference_rtl_sha256": _sha256(_text_bytes(row.reference_rtl)),
                "original_testbench_sha256": _sha256(_text_bytes(row.testbench)),
                "corrected_testbench_sha256": _sha256(corrected.encode("utf-8")),
                "correction_version": correction_version,
                "correction_reason": "retry compatibility test",
                "authoring_method": "trusted_manual_public_spec",
                "reference_modified": False,
                "reference_copied_to_support": False,
                "testbench_path": f"tasks/{row.source_id}/testbench.sv",
                "support_files": [],
                "dependency_closure": "passed",
                "verification_readiness": "executable_ready",
                "qualification_status": "qualified",
                "mutation_contracts": [],
                "static_audit": {},
                "frozen_split_sha256": "a" * 64,
                "source_tree_sha256": "b" * 64,
                "public_specification_sha256": "c" * 64,
                "selection_ids_sha256": "d" * 64,
                "selection_report_sha256": "e" * 64,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    overlay, overlay_errors, correction_rows = overlay_source_rows(
        rows,
        manifest,
        root,
        expected_correction_version=correction_version,
    )

    assert overlay_errors == []
    assert set(correction_rows) == {row.source_id}
    assert overlay[0].testbench == corrected


def test_export_overlay_keeps_correction_bytes_private(tmp_path: Path) -> None:
    source = make_checkout(tmp_path, rows=1)
    from scripts.dataset.rtl_generation_preparation import discover_source_rows, _sha256, _text_bytes, _task_id

    rows, errors = discover_source_rows(source)
    assert errors == []
    row = rows[0]
    row.source_commit = COMMIT
    row.provenance["source_commit"] = COMMIT
    corrected = "module tb; TopModule dut(); initial $display(\"Mismatches: %0d\", 0); endmodule\n"
    root = tmp_path / "correction"
    path = root / "tasks" / row.source_id / "testbench.sv"
    path.parent.mkdir(parents=True)
    path.write_text(corrected, encoding="utf-8")
    manifest = root / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "schema_version": CORRECTION_ROW_SCHEMA_VERSION,
            "source_dataset": row.source_dataset,
            "source_id": row.source_id,
            "task_id": _task_id(row),
            "split": "train",
            "design_family": row.design_family,
            "top_module": "TopModule",
            "upstream_commit": COMMIT,
            "original_prompt_sha256": _sha256(_text_bytes(row.specification)),
            "original_reference_rtl_sha256": _sha256(_text_bytes(row.reference_rtl)),
            "original_testbench_sha256": _sha256(_text_bytes(row.testbench)),
            "corrected_testbench_sha256": _sha256(corrected),
            "correction_version": "assetfix_v002",
            "correction_reason": "test",
            "authoring_method": "trusted_manual_public_spec",
            "reference_modified": False,
            "reference_copied_to_support": False,
            "testbench_path": f"tasks/{row.source_id}/testbench.sv",
            "support_files": [],
            "dependency_closure": "passed",
            "verification_readiness": "executable_ready",
            "negative_mutation_validation": _mutation_contract("Prob001_zero"),
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    public = tmp_path / "public"
    private = tmp_path / "private"
    result, code = export_generation_normalization_batches(
        source,
        public,
        private,
        batch_size=1,
        limit=1,
        source_commit=COMMIT,
        correction_manifest=manifest,
        correction_root=root,
    )
    assert code == 0, result
    assert corrected not in (public / "batch_001.json").read_text(encoding="utf-8")
    task_id = _task_id(row)
    assert (private / "workspace" / task_id / "testbench.sv").read_text(encoding="utf-8") == corrected
