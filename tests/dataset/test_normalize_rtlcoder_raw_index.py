from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.normalize_rtlcoder_raw_index import normalize_rtlcoder_raw_index
from scripts.dataset.rtl_extract import module_names
from tests.dataset.conftest import ROOT, write_rows


def _raw_row(source_id: str, instruction_text: str, rtl_code: str, design_family: str = "general_rtl") -> dict:
    return {
        "schema_version": "rtlcoder_raw_index_v0.1",
        "created_by": "import_rtlcoder_dataset",
        "source_dataset": "rtlcoder_resyn27k",
        "provenance": "external_rtlcoder_gpt_generated_unverified",
        "source_id": source_id,
        "source_record_index": 0,
        "source_line_number": 1,
        "instruction_field": "Instruction",
        "rtl_field": "Response",
        "instruction_text": instruction_text,
        "rtl_code": rtl_code,
        "detected_module_names": module_names(rtl_code),
        "rough_design_family": design_family,
        "import_warnings": [],
    }


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_normalizer_accepts_valid_raw_index_fixture(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    write_rows(
        raw,
        [
            _raw_row(
                "rtlcoder_resyn27k_000001",
                "Design an 8-bit counter with active-low reset.",
                "module counter8(input logic clk, input logic rst_n, output logic [7:0] count_q);\n"
                "  always_ff @(posedge clk or negedge rst_n) begin\n"
                "    if (!rst_n) count_q <= '0;\n"
                "    else count_q <= count_q + 8'd1;\n"
                "  end\n"
                "endmodule\n",
                design_family="counter",
            )
        ],
    )
    result, code = normalize_rtlcoder_raw_index(
        raw,
        tmp_path / "tasks.jsonl",
        tmp_path / "report.md",
        tmp_path / "report.json",
    )
    assert code == 0, result
    assert result["emitted_rows"] == 1


def test_normalizer_emits_rtl_task_and_preserves_prompt_and_rtl(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    instruction = "Implement a D flip-flop."
    rtl = (
        "module dff(input logic clk, input logic d, output logic q);\n"
        "  always_ff @(posedge clk) q <= d;\n"
        "endmodule\n"
    )
    write_rows(raw, [_raw_row("rtlcoder_resyn27k_000001", instruction, rtl, design_family="register")])
    output = tmp_path / "tasks.jsonl"
    result, code = normalize_rtlcoder_raw_index(raw, output, tmp_path / "report.md", tmp_path / "report.json")
    assert code == 0, result
    row = _load_jsonl(output)[0]
    assert row["schema_version"] == "rtl_task_v0.1"
    assert row["prompt"] == instruction
    assert row["artifacts"]["rtl_code"] == rtl
    assert row["source_id"] == "rtlcoder_resyn27k_000001_reference"


def test_normalizer_sets_reference_role_and_null_tool_checks(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    rtl = "module mux2(input logic sel, input logic a, input logic b, output logic y); assign y = sel ? a : b; endmodule\n"
    write_rows(raw, [_raw_row("rtlcoder_resyn27k_000001", "Implement a mux.", rtl, design_family="mux")])
    output = tmp_path / "tasks.jsonl"
    result, code = normalize_rtlcoder_raw_index(raw, output, tmp_path / "report.md", tmp_path / "report.json")
    assert code == 0, result
    row = _load_jsonl(output)[0]
    assert row["source_rtl_role"] == "reference_rtl"
    assert row["design_context"]["source_rtl_role"] == "reference_rtl"
    assert set(row["tool_checks"]) == {"equivalence", "lint", "parse", "power", "simulation", "synthesis", "toggle"}
    assert all(value is None for value in row["tool_checks"].values())
    assert row["review_status"] == "draft"
    assert row["approval_status"] == "not_approved"
    assert row["promotion_allowed"] is False


def test_normalizer_skips_multi_module_rows_when_single_module_only_is_set(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    single = "module one(input logic a, output logic y); assign y = a; endmodule\n"
    multi = (
        "module helper(input logic a, output logic y); assign y = a; endmodule\n"
        "module top(input logic a, output logic y); helper u0(.a(a), .y(y)); endmodule\n"
    )
    write_rows(
        raw,
        [
            _raw_row("rtlcoder_resyn27k_000001", "single", single),
            _raw_row("rtlcoder_resyn27k_000002", "multi", multi),
        ],
    )
    output = tmp_path / "tasks.jsonl"
    result, code = normalize_rtlcoder_raw_index(
        raw,
        output,
        tmp_path / "report.md",
        tmp_path / "report.json",
        single_module_only=True,
    )
    assert code == 0, result
    rows = _load_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["source_id"] == "rtlcoder_resyn27k_000001_reference"
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["skip_reason_counts"]["single_module_only_requires_exactly_one_module"] == 1


def test_normalizer_does_not_write_to_data_golden(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    write_rows(raw, [_raw_row("rtlcoder_resyn27k_000001", "single", "module one(input logic a, output logic y); assign y = a; endmodule\n")])
    output = ROOT / "data" / "golden" / "rtlcoder_normalization_pytest_tmp.jsonl"
    if output.exists():
        output.unlink()
    result, code = normalize_rtlcoder_raw_index(raw, output, tmp_path / "report.md", tmp_path / "report.json")
    assert code == 1
    assert "data/golden" in " ".join(result["errors"])
    assert not output.exists()


def test_normalizer_cli_json_output_is_parseable(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    write_rows(raw, [_raw_row("rtlcoder_resyn27k_000001", "single", "module one(input logic a, output logic y); assign y = a; endmodule\n")])
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/dataset/normalize_rtlcoder_raw_index.py",
            "--input",
            str(raw),
            "--output",
            str(tmp_path / "tasks.jsonl"),
            "--report-md",
            str(tmp_path / "report.md"),
            "--report-json",
            str(tmp_path / "report.json"),
            "--single-module-only",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["emitted_rows"] == 1
