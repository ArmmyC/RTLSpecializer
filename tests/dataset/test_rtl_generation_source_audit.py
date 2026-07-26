from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_generation_preparation import _repository_path_state, audit_source_rows, write_audit_reports
from tests.dataset.rtl_generation_test_helpers import make_checkout


def test_audit_counts_metadata_without_raw_content(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=2)
    report = audit_source_rows(source)
    assert report["total_rows"] == 2
    assert report["rows_with_nonempty_prompt_or_specification"] == 2
    assert report["rows_with_reference_rtl"] == 2
    assert report["rows_with_testbench"] == 2
    assert report["rows_appearing_executable_ready"] == 2
    assert report["readiness_categories"] == {"executable_ready": 2}
    serialized = json.dumps(report)
    assert "module RefModule" not in serialized
    assert "synthetic checker text" not in serialized
    assert all("content_hashes" in sample for sample in report["samples"])


def test_audit_detects_symlinked_input(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    target = source / "Prob001_task_prompt.txt"
    moved = source / "prompt-real.txt"
    target.rename(moved)
    target.symlink_to(moved)
    report = audit_source_rows(source)
    assert report["symlinked_inputs"]
    assert any("symlink" in error for error in report["errors"])


def test_audit_reports_are_local_metadata_only(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    report = audit_source_rows(source)
    json_path = tmp_path / "audit.json"
    markdown_path = tmp_path / "audit.md"
    write_audit_reports(report, json_path, markdown_path)
    text = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
    assert "module RefModule" not in text
    assert "testbench" not in text.lower() or "testbench contents" in text.lower()


def test_audit_classifies_unconfirmed_license_and_missing_testbench(tmp_path) -> None:
    source = tmp_path / "normalized.json"
    source.write_text(json.dumps([{
        "source_id": "rtlcoder_001",
        "source_dataset": "RTLCoder",
        "license": "unconfirmed_upstream_license",
        "prompt": "Implement module named TopModule. - input a - output y",
        "artifacts": {"rtl_code": "module RefModule(input a, output y); assign y = a; endmodule", "testbench": None},
        "provenance": {"public_dataset_name": "RTLCoder"},
    }]) + "\n", encoding="utf-8")
    report = audit_source_rows(source)
    assert report["readiness_categories"] == {"license_blocked": 1}
    assert report["rows_with_placeholder_or_missing_license"] == 1


def test_audit_path_classification_is_conservative() -> None:
    assert _repository_path_state(Path("data/.local_data/verilog-eval-main/dataset_spec-to-rtl")) == "ignored_local"
    assert _repository_path_state(Path("README.md")) == "tracked"
    assert _repository_path_state(Path("/external/private/source")) == "untracked_or_unknown"
