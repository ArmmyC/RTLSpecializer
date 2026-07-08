from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.synthesize_rtl_bug_variants import synthesize_rtl_bug_variants
from scripts.dataset.rtl_extract import module_names, summarize_rtl
from tests.dataset.conftest import ROOT, write_rows


def _reference_task(source_id: str, rtl_code: str, prompt: str = "Review this RTL.", design_family: str = "general_rtl") -> dict:
    modules = module_names(rtl_code)
    module_name = modules[0] if len(modules) == 1 else None
    return {
        "schema_version": "rtl_task_v0.1",
        "created_by": "normalize_rtlcoder_raw_index",
        "source_id": source_id,
        "source_dataset": "rtlcoder_resyn27k",
        "license": "unconfirmed_upstream_license",
        "provenance": {
            "origin": "external_rtlcoder_gpt_generated_unverified",
            "public_dataset_name": "RTLCoder Resyn27k",
            "public_dataset_url": None,
            "source_commit": None,
            "notes": "fixture",
        },
        "design_family": design_family,
        "task_type": "rtl_bug_review",
        "user_goal": "find_correctness_bug",
        "domain": "digital_rtl",
        "prompt": prompt,
        "source_rtl_role": "reference_rtl",
        "tool_checks": {
            "equivalence": None,
            "lint": None,
            "parse": None,
            "power": None,
            "simulation": None,
            "synthesis": None,
            "toggle": None,
        },
        "design_context": {
            "target_domain": "digital_rtl_public_benchmark",
            "priority": ["correctness", "low_switching_activity", "low_area"],
            "timing_policy": "timing_is_constraint_not_reward",
            "source_rtl_role": "reference_rtl",
            "target_module_name": module_name,
            "rtl_module_name": module_name,
            "interface_ports_from_prompt": [],
            "prompt_embedded_candidate_rtl": False,
            "prompt_embedded_context_rtl": False,
        },
        "artifacts": {
            "rtl_code": rtl_code,
            "before_rtl_code": None,
            "after_rtl_code": None,
            "testbench": None,
            "lint_log": None,
            "synthesis_report": None,
            "toggle_report": None,
        },
        "extracted_rtl_summary": summarize_rtl({"rtl_code": rtl_code}),
        "constraints": {
            "preserve_top_level_interface": True,
            "preserve_cycle_level_behavior": True,
            "preserve_reset_behavior": True,
            "do_not_claim_power_without_power_report": True,
            "prefer_minimal_patch": True,
        },
        "notes": ["reference fixture"],
        "assumptions": ["fixture"],
        "required_output": [
            "claim_levels",
            "functional_risk",
            "issue_summary",
            "safe_optimization",
            "space_reasoning",
            "time_reasoning",
            "verification_plan",
        ],
        "review_status": "draft",
        "approval_status": "not_approved",
        "promotion_allowed": False,
    }


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_synthetic_mutator_creates_before_rtl_code_and_keeps_original_rtl(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    rtl = (
        "module mux2(input logic sel, input logic a, input logic b, output logic y);\n"
        "  assign y = sel ? a : b;\n"
        "endmodule\n"
    )
    write_rows(tasks, [_reference_task("rtlcoder_resyn27k_000001_reference", rtl, design_family="mux")])
    output = tmp_path / "synthetic.jsonl"
    result, code = synthesize_rtl_bug_variants(
        tasks,
        output,
        tmp_path / "report.md",
        tmp_path / "report.json",
        max_source_rows=1,
        variants_per_row=1,
        seed=42,
    )
    assert code == 0, result
    row = _load_jsonl(output)[0]
    assert row["artifacts"]["rtl_code"] == rtl
    assert row["artifacts"]["before_rtl_code"] != rtl
    assert row["artifacts"]["before_rtl_code"] is not None


def test_synthetic_mutator_marks_synthetic_bug_and_not_approved(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    rtl = (
        "module mux2(input logic sel, input logic a, input logic b, output logic y);\n"
        "  assign y = sel ? a : b;\n"
        "endmodule\n"
    )
    write_rows(tasks, [_reference_task("rtlcoder_resyn27k_000001_reference", rtl, design_family="mux")])
    output = tmp_path / "synthetic.jsonl"
    result, code = synthesize_rtl_bug_variants(
        tasks,
        output,
        tmp_path / "report.md",
        tmp_path / "report.json",
        max_source_rows=1,
        variants_per_row=1,
        seed=42,
    )
    assert code == 0, result
    row = _load_jsonl(output)[0]
    assert row["synthetic_bug"] is True
    assert row["approval_status"] == "not_approved"
    assert row["promotion_allowed"] is False
    assert row["review_status"] == "synthetic_draft"
    assert row["design_context"]["prompt_embedded_candidate_rtl"] is True


def test_synthetic_mutator_is_deterministic_with_same_seed(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    rtl = (
        "module mux2(input logic sel, input logic a, input logic b, output logic y);\n"
        "  assign y = sel ? a : b;\n"
        "endmodule\n"
    )
    write_rows(tasks, [_reference_task("rtlcoder_resyn27k_000001_reference", rtl, design_family="mux")])
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first_result, first_code = synthesize_rtl_bug_variants(
        tasks,
        first,
        tmp_path / "first.md",
        tmp_path / "first.json",
        max_source_rows=1,
        variants_per_row=1,
        seed=42,
    )
    second_result, second_code = synthesize_rtl_bug_variants(
        tasks,
        second,
        tmp_path / "second.md",
        tmp_path / "second.json",
        max_source_rows=1,
        variants_per_row=1,
        seed=42,
    )
    assert first_code == second_code == 0, (first_result, second_result)
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_synthetic_mutator_skips_rows_with_no_safe_mutation(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    rtl = "module passthrough(input logic a, output logic y); assign y = a; endmodule\n"
    write_rows(tasks, [_reference_task("rtlcoder_resyn27k_000001_reference", rtl)])
    result, code = synthesize_rtl_bug_variants(
        tasks,
        tmp_path / "synthetic.jsonl",
        tmp_path / "report.md",
        tmp_path / "report.json",
        max_source_rows=1,
        variants_per_row=1,
        seed=42,
    )
    assert code == 1
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["skip_reason_counts"]["no_safe_mutation_pattern"] == 1


def test_synthetic_mutator_report_includes_bug_type_counts(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    rtl = (
        "module mux2(input logic sel, input logic a, input logic b, output logic y);\n"
        "  assign y = sel ? a : b;\n"
        "endmodule\n"
    )
    write_rows(tasks, [_reference_task("rtlcoder_resyn27k_000001_reference", rtl, design_family="mux")])
    result, code = synthesize_rtl_bug_variants(
        tasks,
        tmp_path / "synthetic.jsonl",
        tmp_path / "report.md",
        tmp_path / "report.json",
        max_source_rows=1,
        variants_per_row=1,
        seed=42,
    )
    assert code == 0, result
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["bug_type_counts"] == {"wrong_mux_select_polarity": 1}


def test_synthetic_mutator_cli_json_output_is_parseable(tmp_path) -> None:
    tasks = tmp_path / "tasks.jsonl"
    rtl = (
        "module mux2(input logic sel, input logic a, input logic b, output logic y);\n"
        "  assign y = sel ? a : b;\n"
        "endmodule\n"
    )
    write_rows(tasks, [_reference_task("rtlcoder_resyn27k_000001_reference", rtl, design_family="mux")])
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/dataset/synthesize_rtl_bug_variants.py",
            "--input",
            str(tasks),
            "--output",
            str(tmp_path / "synthetic.jsonl"),
            "--report-md",
            str(tmp_path / "report.md"),
            "--report-json",
            str(tmp_path / "report.json"),
            "--max-source-rows",
            "1",
            "--variants-per-row",
            "1",
            "--seed",
            "42",
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
