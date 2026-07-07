from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.constants import REQUIRED_OUTPUT
from scripts.dataset.verilog_eval_normalization_batches import (
    export_verilog_eval_normalization_batches,
    validate_verilog_eval_normalized_batch,
)
from tests.dataset.conftest import ROOT


FIXTURE = ROOT / "tests" / "fixtures" / "verilog_eval_review" / "manifest.jsonl"
DIRECTORY_FIXTURE = ROOT / "tests" / "fixtures" / "verilog_eval_review" / "local_checkout"
PROMPT_TEMPLATE = ROOT / "docs" / "dataset" / "llm_rtl_task_normalization_prompt.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized_rows_from_raw(batch_path: Path) -> list[dict]:
    payload = _load_json(batch_path)
    rows: list[dict] = []
    for raw in payload["rows"]:
        rows.append({
            "source_id": raw["source_id"],
            "source_dataset": raw["source_dataset"],
            "license": raw["license"],
            "provenance": raw["provenance"],
            "design_family": raw["design_family"],
            "task_type": raw["task_type"],
            "user_goal": raw["user_goal"],
            "schema_version": "rtl_task_v0.1",
            "domain": "digital_rtl",
            "prompt": raw["raw_prompt"],
            "design_context": {
                "target_domain": "digital_rtl_public_benchmark",
                "priority": ["correctness", "low_switching_activity", "low_area"],
                "timing_policy": "timing_is_constraint_not_reward",
            },
            "artifacts": {
                "rtl_code": raw["raw_reference_rtl"],
                "before_rtl_code": None,
                "after_rtl_code": None,
                "testbench": raw["raw_testbench"],
                "lint_log": None,
                "synthesis_report": None,
                "toggle_report": None,
            },
            "extracted_rtl_summary": {
                "top_module": None,
                "clock_signals": [],
                "reset_signals": [],
                "registered_signals": [],
                "combinational_blocks": [],
                "suspected_fsm_signals": [],
                "suspected_counters": [],
                "unused_enable_signals": [],
                "activity_hotspots": [],
            },
            "constraints": {
                "preserve_top_level_interface": True,
                "preserve_cycle_level_behavior": True,
                "preserve_reset_behavior": True,
                "do_not_claim_power_without_power_report": True,
                "prefer_minimal_patch": True,
            },
            "assumptions": [
                "Normalized from raw VerilogEval source text.",
                "No tool evidence was added during normalization.",
            ],
            "required_output": sorted(REQUIRED_OUTPUT),
        })
    return rows


def test_exports_deterministic_batch_files(tmp_path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first, first_code = export_verilog_eval_normalization_batches(FIXTURE, first_dir, batch_size=2)
    second, second_code = export_verilog_eval_normalization_batches(FIXTURE, second_dir, batch_size=2)
    assert first_code == second_code == 0, (first, second)
    assert first["batch_files"]
    assert (first_dir / "batch_001.json").read_text(encoding="utf-8") == (second_dir / "batch_001.json").read_text(encoding="utf-8")
    assert (first_dir / "batch_002.json").read_text(encoding="utf-8") == (second_dir / "batch_002.json").read_text(encoding="utf-8")


def test_batch_size_limit_and_start_index_are_respected(tmp_path) -> None:
    result, code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2, limit=2, start_index=1)
    assert code == 0, result
    assert result["exported_rows"] == 2
    assert len(result["batch_files"]) == 1
    payload = _load_json(tmp_path / "batches" / "batch_001.json")
    assert payload["row_count"] == 2
    assert [row["source_id"] for row in payload["rows"]] == ["fsm_002", "shift_003"]


def test_refuses_overwrite_without_force_and_force_preserves_unknown_files(tmp_path) -> None:
    output_dir = tmp_path / "batches"
    first, first_code = export_verilog_eval_normalization_batches(FIXTURE, output_dir, batch_size=1)
    assert first_code == 0, first
    notes = output_dir / "reviewer_notes.md"
    notes.write_text("keep me\n", encoding="utf-8")

    failed, failed_code = export_verilog_eval_normalization_batches(FIXTURE, output_dir, batch_size=2)
    assert failed_code == 1
    assert "managed batch files" in failed["errors"][0]

    forced, forced_code = export_verilog_eval_normalization_batches(FIXTURE, output_dir, batch_size=2, force=True)
    assert forced_code == 0, forced
    assert notes.read_text(encoding="utf-8") == "keep me\n"
    assert not (output_dir / "batch_003.json").exists()
    assert (output_dir / "batch_001.json").exists()
    assert (output_dir / "batch_002.json").exists()


def test_preserves_multiline_prompt_rtl_and_testbench_text(tmp_path) -> None:
    result, code = export_verilog_eval_normalization_batches(DIRECTORY_FIXTURE, tmp_path / "batches", batch_size=10)
    assert code == 0, result
    payload = _load_json(tmp_path / "batches" / "batch_001.json")
    row = payload["rows"][0]
    expected_prompt = (DIRECTORY_FIXTURE / "dataset_spec-to-rtl" / "Prob001_counter_prompt.txt").read_text(encoding="utf-8")
    expected_rtl = (DIRECTORY_FIXTURE / "dataset_spec-to-rtl" / "Prob001_counter_ref.sv").read_text(encoding="utf-8")
    expected_tb = (DIRECTORY_FIXTURE / "dataset_spec-to-rtl" / "Prob001_counter_test.sv").read_text(encoding="utf-8")
    assert row["raw_prompt"] == expected_prompt
    assert row["raw_reference_rtl"] == expected_rtl
    assert row["raw_testbench"] == expected_tb
    assert "\n" in row["raw_reference_rtl"]
    assert "\n" in row["raw_testbench"]


def test_cli_json_output_is_parseable(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/export_verilog_eval_normalization_batches.py",
            "--input", str(FIXTURE),
            "--output-dir", str(tmp_path / "batches"),
            "--batch-size", "2",
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
    assert payload["exported_rows"] == 3
    assert len(payload["batch_files"]) == 2


def test_generated_rows_contain_provenance_and_source_id(tmp_path) -> None:
    result, code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches")
    assert code == 0, result
    payload = _load_json(tmp_path / "batches" / "batch_001.json")
    row = payload["rows"][0]
    assert row["source_id"] == "counter_001"
    assert row["provenance"]["public_dataset_name"] == "Synthetic VerilogEval Review Fixture"
    assert row["source_dataset"] == "public_verilog_eval"
    assert set(row["tool_checks"]) == {"equivalence", "lint", "parse", "power", "simulation", "synthesis", "toggle"}


def test_prompt_template_exists_and_mentions_no_invention_rules() -> None:
    text = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "return valid json only" in lowered
    assert "do not create rtl_answer_v0.1" in lowered
    assert "do not invent logs, reports, tool checks, measurements, or verification results" in lowered
    assert "set missing tool-artifact fields to null" in lowered


def test_validator_accepts_valid_normalized_batch(tmp_path) -> None:
    export_result, export_code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2)
    assert export_code == 0, export_result
    raw_batch = tmp_path / "batches" / "batch_001.json"
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps({"rows": _normalized_rows_from_raw(raw_batch)}, indent=2) + "\n", encoding="utf-8")

    result, code = validate_verilog_eval_normalized_batch(raw_batch, normalized)
    assert code == 0, result
    assert result["ok"] is True


def test_validator_rejects_missing_source_id(tmp_path) -> None:
    export_result, export_code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2)
    assert export_code == 0, export_result
    raw_batch = tmp_path / "batches" / "batch_001.json"
    rows = _normalized_rows_from_raw(raw_batch)
    rows[0].pop("source_id")
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    result, code = validate_verilog_eval_normalized_batch(raw_batch, normalized)
    assert code == 1
    assert any("missing source_id" in error for error in result["errors"])


def test_validator_rejects_wrong_schema_version(tmp_path) -> None:
    export_result, export_code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2)
    assert export_code == 0, export_result
    raw_batch = tmp_path / "batches" / "batch_001.json"
    rows = _normalized_rows_from_raw(raw_batch)
    rows[0]["schema_version"] = "rtl_answer_v0.1"
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    result, code = validate_verilog_eval_normalized_batch(raw_batch, normalized)
    assert code == 1
    assert any("schema_version 'rtl_task_v0.1'" in error for error in result["errors"])


def test_validator_rejects_answer_fields_prompt_drift_and_invented_tool_evidence(tmp_path) -> None:
    export_result, export_code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2)
    assert export_code == 0, export_result
    raw_batch = tmp_path / "batches" / "batch_001.json"
    rows = _normalized_rows_from_raw(raw_batch)
    rows[0]["issue_summary"] = []
    rows[0]["prompt"] = rows[0]["prompt"] + "\nextra"
    rows[0]["artifacts"]["lint_log"] = "invented lint output"
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps({"rows": rows}, indent=2) + "\n", encoding="utf-8")

    result, code = validate_verilog_eval_normalized_batch(raw_batch, normalized)
    assert code == 1
    assert any("assistant-answer field 'issue_summary'" in error for error in result["errors"])
    assert any("changed prompt text" in error for error in result["errors"])
    assert any("invented tool artifact lint_log" in error for error in result["errors"])


def test_validator_rejects_rtl_and_testbench_drift(tmp_path) -> None:
    export_result, export_code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2)
    assert export_code == 0, export_result
    raw_batch = tmp_path / "batches" / "batch_001.json"
    rows = _normalized_rows_from_raw(raw_batch)
    rows[0]["artifacts"]["rtl_code"] = "module drift; endmodule\n"
    rows[0]["artifacts"]["testbench"] = "module tb; endmodule\n"
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    result, code = validate_verilog_eval_normalized_batch(raw_batch, normalized)
    assert code == 1
    assert any("changed artifacts.rtl_code" in error for error in result["errors"])
    assert any("changed artifacts.testbench" in error for error in result["errors"])


def test_validator_cli_json_output_is_parseable(tmp_path) -> None:
    export_result, export_code = export_verilog_eval_normalization_batches(FIXTURE, tmp_path / "batches", batch_size=2)
    assert export_code == 0, export_result
    raw_batch = tmp_path / "batches" / "batch_001.json"
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps(_normalized_rows_from_raw(raw_batch), indent=2) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/validate_verilog_eval_normalized_batch.py",
            "--raw-batch", str(raw_batch),
            "--normalized", str(normalized),
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
    assert payload["expected_rows"] == 2
