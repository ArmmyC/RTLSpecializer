from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

from scripts.dataset.rtl_generation_preparation import (
    SourceRow,
    _clock_reset_hints,
    _interface_hints,
    _readiness,
    _task_id,
    _verification_dependency_report,
    export_generation_normalization_batches,
)
from scripts.dataset.rtl_generation_asset_corrections import load_correction_manifest
from tests.dataset.rtl_generation_test_helpers import load_batch, make_checkout


def test_v003_correction_manifest_is_accepted_by_generation_overlay_loader(tmp_path: Path) -> None:
    correction_root = tmp_path / "correction"
    testbench = correction_root / "tasks" / "Prob029_m2014_q4g" / "testbench.sv"
    testbench.parent.mkdir(parents=True)
    content = b'''module tb;
  logic a;
  logic y;
  TopModule dut(.a(a), .y(y));
  initial begin
    $display("Mismatches: %0d", 0);
    $finish;
  end
endmodule
'''
    testbench.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    row = {
        "schema_version": "rtl_verification_asset_correction_row_v0.2",
        "source_dataset": "VerilogEval",
        "source_id": "Prob029_m2014_q4g",
        "task_id": "task_prob029",
        "split": "train",
        "top_module": "TopModule",
        "upstream_commit": "a" * 40,
        "original_prompt_sha256": "b" * 64,
        "original_reference_rtl_sha256": "c" * 64,
        "original_testbench_sha256": "d" * 64,
        "corrected_testbench_sha256": digest,
        "correction_version": "assetfix_v003",
        "reference_modified": False,
        "reference_copied_to_support": False,
        "testbench_path": "tasks/Prob029_m2014_q4g/testbench.sv",
        "support_files": [],
        "dependency_closure": "passed",
        "verification_readiness": "executable_ready",
    }
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

    rows, errors = load_correction_manifest(
        manifest,
        correction_root,
        expected_correction_version="assetfix_v003",
    )

    assert errors == []
    assert rows == [row]


def test_interface_hints_preserve_parenthetical_bit_widths() -> None:
    ports = _interface_hints(
        """
        - input A (2 bits)
        - input data (8 bits)
        - output result (3 bits)
        """
    )

    assert [port["width_bits"] for port in ports] == [2, 8, 3]
    assert [port["packed_range"] for port in ports] == [None, None, None]


def test_clock_reset_hints_preserve_explicit_reset_contract() -> None:
    specification = """
    - input clk
    - input r
    - output q

    Implement a D flip flop with active high synchronous reset.
    """
    ports = _interface_hints(specification)

    clocks, resets = _clock_reset_hints(specification, ports)

    assert clocks == [{"signal": "clk", "edge": "posedge"}]
    assert resets == [{
        "signal": "r",
        "active_level": "high",
        "synchronous": True,
    }]


def test_short_r_is_not_a_reset_without_reset_language() -> None:
    specification = """
    - input clk
    - input r
    - output q

    Capture the input value on each positive clock edge.
    """
    ports = _interface_hints(specification)

    _, resets = _clock_reset_hints(specification, ports)

    assert resets == []


def _dependency_row(
    testbench: str,
    *,
    reference: str = "module ReferenceOnly; endmodule\n",
    support: dict[str, bytes] | None = None,
) -> SourceRow:
    return SourceRow(
        source_id="synthetic_dependency_case",
        source_dataset="synthetic",
        design_family="combinational",
        specification=(
            "Implement module named TopModule with the following interface.\n"
            "- input a\n"
            "- output y\n"
        ),
        reference_rtl=reference,
        testbench=testbench,
        support_files=support or {},
        license="MIT",
        provenance={"public_dataset_name": "synthetic"},
        top_module_hint="TopModule",
        interface_hints=[
            {"name": "a", "direction": "input"},
            {"name": "y", "direction": "output"},
        ],
    )


def _self_contained_testbench(extra: str = "") -> str:
    return (
        "module tb;\n"
        f"{extra}"
        "  logic a;\n"
        "  logic y;\n"
        "  TopModule dut(.a(a), .y(y));\n"
        "  initial $display(\"mismatch_count_v1: 0\");\n"
        "endmodule\n"
    )


def test_dependency_closure_accepts_self_contained_testbench() -> None:
    row = _dependency_row(_self_contained_testbench())
    assert _readiness(row) == ("executable_ready", [])
    report = _verification_dependency_report(row)
    assert report.unresolved_modules == frozenset()
    assert report.unresolved_packages == frozenset()


def test_dependency_closure_accepts_explicit_support_module() -> None:
    row = _dependency_row(
        _self_contained_testbench("  Helper helper(.a(a), .y(y));\n"),
        support={"helper.sv": b"module Helper(input a, output y); endmodule\n"},
    )
    assert _readiness(row) == ("executable_ready", [])


def test_reference_only_helper_downgrades_readiness_without_copying_reference() -> None:
    row = _dependency_row(
        _self_contained_testbench("  ReferenceHelper helper();\n"),
        reference=(
            "module ReferenceOnly; endmodule\n"
            "module ReferenceHelper; endmodule\n"
        ),
    )
    readiness, reasons = _readiness(row)
    assert readiness == "needs_testbench"
    assert reasons == [
        "testbench depends on a non-candidate module that is available only in private reference material",
    ]
    assert row.support_files == {}


def test_missing_helper_downgrades_readiness() -> None:
    readiness, reasons = _readiness(
        _dependency_row(_self_contained_testbench("  MissingHelper helper();\n"))
    )
    assert readiness == "needs_testbench"
    assert reasons == ["testbench has an unresolved non-candidate dependency"]


def test_unresolved_package_requires_interface_review() -> None:
    row = _dependency_row(
        "import MissingPackage::*;\n" + _self_contained_testbench()
    )
    readiness, reasons = _readiness(row)
    assert readiness == "needs_interface_review"
    assert reasons == ["testbench has unresolved package dependencies"]


def test_package_and_interface_dependencies_resolve_from_support() -> None:
    row = _dependency_row(
        "import HelperPackage::*;\n"
        + _self_contained_testbench("  HelperInterface bus();\n"),
        support={
            "helper.sv": (
                b"package HelperPackage; endpackage\n"
                b"interface HelperInterface; endinterface\n"
            ),
        },
    )
    assert _readiness(row) == ("executable_ready", [])


def test_dependency_lexer_ignores_comments_strings_and_declarations() -> None:
    row = _dependency_row(
        """module tb;
  // MissingInComment fake();
  string text = "MissingInString fake();";
  function void helper(input logic value);
  endfunction
  logic a;
  logic y;
  TopModule dut(.a(a), .y(y));
endmodule
"""
    )
    report = _verification_dependency_report(row)
    assert report.probable_instantiations == ("TopModule",)
    assert report.unresolved_modules == frozenset()


def test_reference_bytes_cannot_be_repackaged_as_support() -> None:
    reference = "module ReferenceOnly; endmodule\n"
    readiness, reasons = _readiness(
        _dependency_row(
            _self_contained_testbench(),
            reference=reference,
            support={"copied.sv": reference.encode("utf-8")},
        )
    )
    assert readiness == "needs_testbench"
    assert reasons == ["support files must not contain reference RTL bytes"]


def test_public_source_id_directory_component_is_not_a_path_leak(tmp_path) -> None:
    source_root = tmp_path / "verilog_eval_assetfix_v1" / "prob001_zero"
    source_root.mkdir(parents=True)
    source_path = source_root / "source.json"
    source_path.write_text(
        json.dumps([{
            "source_id": "Prob001_zero",
            "source_dataset": "VerilogEval_assetfix_v1",
            "design_family": "combinational",
            "specification": "Implement module named TopModule. - output zero",
            "artifacts": {
                "rtl_code": "module ReferenceOnly; endmodule",
                "testbench": "module tb; TopModule dut(); endmodule",
                "support_files": {},
            },
            "license": "MIT",
            "provenance": {
                "public_dataset_name": "VerilogEval",
                "public_dataset_url": "https://example.invalid/verilog-eval",
                "source_commit": None,
                "license": "MIT",
                "original_source_id": "Prob001_zero",
            },
            "design_context": {
                "target_module_name": "TopModule",
                "interface_ports_from_prompt": [{
                    "name": "zero",
                    "direction": "output",
                    "declaration": "output zero",
                    "packed_range": None,
                    "width_bits": 1,
                    "signed": False,
                    "description": None,
                }],
            },
        }]),
        encoding="utf-8",
    )
    result, code = export_generation_normalization_batches(
        source_path,
        tmp_path / "public",
        tmp_path / "private",
        batch_size=1,
        limit=1,
    )
    assert code == 0, result


def test_manual_normalization_prompt_matches_generation_task_schema() -> None:
    prompt_path = Path(__file__).resolve().parents[2] / "docs/dataset/llm_rtl_generation_task_normalization_prompt.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    for field in (
        "schema_version", "task_id", "source_id", "source_dataset", "design_family",
        "language", "specification", "top_module", "interface", "clocking", "reset",
        "latency_contract", "behavioral_constraints", "assumptions", "ambiguities", "provenance",
    ):
        assert f"`{field}`" in prompt or f'"{field}"' in prompt, field
    for field in ("name", "direction", "declaration", "packed_range", "width_bits", "signed", "description"):
        assert f"`{field}`" in prompt, field
    collapsed = " ".join(prompt.split())
    assert "exactly `clock_signal` and `edge`" in collapsed
    assert "exactly `signal`, `active_level`, and `synchronous`" in collapsed
    assert "parenthetical width" in collapsed
    assert "explicitly identifies reset behavior" in collapsed
    for field in ("cycles", "min_cycles", "max_cycles", "throughput_cycles", "description"):
        assert f"`{field}`" in prompt, field
    assert "do not include a top-level `license`" in prompt
    assert "provenance.license" in prompt
    assert "additionalProperties: false" in prompt
    for forbidden in (
        "`raw_specification`", "deterministic hint fields", "reference RTL",
        "testbench content", "private paths", "tool results", "expected vectors",
        "generated RTL",
    ):
        assert forbidden in prompt, forbidden
    assert not re.search(r"\b(?:return|include|output|set|emit)\s+(?:the\s+)?[`\"]constraints[`\"]", prompt, re.IGNORECASE)
    assert '"constraints":' not in prompt


def test_export_separates_public_and_private_bytes_and_is_deterministic(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=3)
    first_public = tmp_path / "public-one"
    first_private = tmp_path / "private-one"
    second_public = tmp_path / "public-two"
    second_private = tmp_path / "private-two"
    first, first_code = export_generation_normalization_batches(source, first_public, first_private, batch_size=2)
    second, second_code = export_generation_normalization_batches(source, second_public, second_private, batch_size=2)
    assert first_code == second_code == 0, (first, second)
    first_payload = load_batch(first_public / "batch_001.json")
    second_payload = load_batch(second_public / "batch_001.json")
    assert first_payload == second_payload
    public_text = (first_public / "batch_001.json").read_text(encoding="utf-8")
    assert "input" not in first_payload
    assert first_payload["source_label"] == "VerilogEval"
    assert "raw_reference_rtl" not in public_text
    assert "raw_testbench" not in public_text
    assert "reference.sv" not in public_text
    row = first_payload["rows"][0]
    assert row["raw_specification"].startswith("Implement module named TopModule")
    assets = [json.loads(line) for line in (first_private / "verification_assets.jsonl").read_text().splitlines() if line.strip()]
    assert len(assets) == 3
    asset = assets[0]
    assert asset["input_hashes"]["reference_rtl_sha256"]
    assert (first_private / asset["reference_rtl_path"]).is_file()
    assert (first_private / asset["testbench_path"]).is_file()
    assert (first_private / asset["reference_rtl_path"]).read_text().startswith("module RefModule")


def test_public_batch_does_not_leak_local_checkout_or_private_paths(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    leaked_source = tmp_path / "data" / ".local_data" / "verilog-eval-main" / "dataset_spec-to-rtl"
    leaked_source.parent.mkdir(parents=True)
    source.rename(leaked_source)
    private = tmp_path / "data" / ".local_data" / "private-verification-output"
    public = tmp_path / "public"
    result, code = export_generation_normalization_batches(leaked_source, public, private)
    assert code == 0, result
    payload = load_batch(public / "batch_001.json")
    text = (public / "batch_001.json").read_text(encoding="utf-8")
    for forbidden in (".local_data", "verilog-eval-main", "dataset_spec-to-rtl", str(leaked_source), str(private), "reference RTL", "testbench text"):
        assert forbidden not in text
    assert payload["source_label"] == "VerilogEval"


def test_task_id_uses_only_canonical_identity_fields() -> None:
    base = SourceRow(
        source_id="source-1", source_dataset="VerilogEval", design_family="sequential",
        specification="prompt", reference_rtl="rtl", testbench="tb", license="MIT",
        provenance={"notes": "one", "public_dataset_url": "https://example.invalid/one"},
        source_path="data/.local_data/first", source_commit="abc123",
    )
    assert _task_id(base) == _task_id(replace(base, provenance={"notes": "changed"}, source_path="/other/private/path"))
    assert _task_id(base) != _task_id(replace(base, source_commit="def456"))
    assert _task_id(base) != _task_id(replace(base, source_id="source-2"))
    assert _task_id(base) != _task_id(replace(base, source_dataset="OtherDataset"))


def test_export_limit_and_support_files_are_preserved(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=4, with_support=True)
    result, code = export_generation_normalization_batches(source, tmp_path / "public", tmp_path / "private", batch_size=5, limit=2)
    assert code == 0, result
    assert result["exported_rows"] == 2
    assets = [json.loads(line) for line in (tmp_path / "private" / "verification_assets.jsonl").read_text().splitlines() if line.strip()]
    assert len(assets) == 2
    assert assets[0]["support_files"]
    support_path = tmp_path / "private" / assets[0]["support_files"][0]
    assert support_path.read_text(encoding="utf-8").startswith("module TopModule")


def test_duplicate_source_ids_and_missing_prompt_fail_before_output(tmp_path) -> None:
    duplicate = [
        {"source_id": "same", "source_dataset": "fixture", "prompt": "a", "artifacts": {"rtl_code": "module A; endmodule"}},
        {"source_id": "same", "source_dataset": "fixture", "prompt": "b", "artifacts": {"rtl_code": "module B; endmodule"}},
    ]
    source = tmp_path / "rows.json"
    source.write_text(json.dumps(duplicate) + "\n", encoding="utf-8")
    result, code = export_generation_normalization_batches(source, tmp_path / "public", tmp_path / "private")
    assert code == 1
    assert any("duplicate source_id" in error for error in result["errors"])
    assert not (tmp_path / "public" / "batch_001.json").exists()
    missing = [{"source_id": "missing", "source_dataset": "fixture", "artifacts": {"rtl_code": "module A; endmodule"}}]
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(json.dumps(missing) + "\n", encoding="utf-8")
    result, code = export_generation_normalization_batches(missing_path, tmp_path / "public-missing", tmp_path / "private-missing")
    assert code == 1
    assert any("missing specification" in error for error in result["errors"])
