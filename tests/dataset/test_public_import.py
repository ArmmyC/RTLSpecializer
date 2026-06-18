from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.adapters import ImportOptions, get_adapter
from scripts.dataset.import_public_dataset import import_public_dataset
from scripts.dataset.split_dataset import split_dataset
from scripts.dataset.validation import validate_dataset_file


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "public_manifest" / "manifest.jsonl"


def test_manifest_adapter_imports_valid_row(tmp_path) -> None:
    output = tmp_path / "draft.jsonl"
    result, code = import_public_dataset("manifest", FIXTURE, output, ImportOptions())
    assert code == 0
    assert result["imported_rows"] == 1
    row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert row["review_status"] == "draft"
    assert row["split"] == "unsplit"
    assert row["created_by"] == "script"
    assert row["messages"][2]["content"]["claim_levels"] == {
        "correctness": "insufficient_evidence",
        "area": "insufficient_evidence",
        "activity": "insufficient_evidence",
        "power": "insufficient_evidence",
    }
    assert validate_dataset_file(output, strict=True).ok


def test_manifest_adapter_rejects_missing_artifact(tmp_path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "id": "missing_001", "source": "public_verilog_eval", "license": "fixture",
            "design_family": "counter", "task_type": "rtl_bug_review", "user_goal": "find_correctness_bug",
            "artifacts": {"rtl_code_path": "missing.v"},
            "provenance": {"public_dataset_name": "Fixture", "public_dataset_url": None, "source_commit": None, "notes": "test"},
        }) + "\n",
        encoding="utf-8",
    )
    result, code = import_public_dataset("manifest", manifest, tmp_path / "out.jsonl", ImportOptions())
    assert code == 1
    assert result["imported_rows"] == 0
    assert result["rejected_examples"] == 1


def test_manifest_adapter_rejects_path_traversal(tmp_path) -> None:
    manifest_dir = tmp_path / "root"
    manifest_dir.mkdir()
    (tmp_path / "outside.v").write_text("module outside; endmodule\n", encoding="utf-8")
    manifest = manifest_dir / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "id": "escape_001", "source": "public_verilog_eval", "license": "fixture",
            "design_family": "counter", "task_type": "rtl_bug_review", "user_goal": "find_correctness_bug",
            "artifacts": {"rtl_code_path": "../outside.v"},
            "provenance": {"public_dataset_name": "Fixture", "public_dataset_url": None, "source_commit": None, "notes": "test"},
        }) + "\n",
        encoding="utf-8",
    )
    result, code = import_public_dataset("manifest", manifest, tmp_path / "out.jsonl", ImportOptions())
    assert code == 1
    rejected = json.loads((tmp_path / "out.rejected.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert "escapes input root" in " ".join(rejected["errors"])
    assert result["rejected_examples"] == 1


def test_imported_draft_rows_are_rejected_by_splitter(tmp_path) -> None:
    output = tmp_path / "draft.jsonl"
    result, code = import_public_dataset("manifest", FIXTURE, output, ImportOptions())
    assert code == 0, result
    split_result, errors = split_dataset(output, tmp_path / "split", (.7, .15, .15), 7)
    assert split_result is None
    assert any("non-training-ready rows require --allow-unreviewed" in error for error in errors)


def test_cli_json_output_is_parseable(tmp_path) -> None:
    output = tmp_path / "draft.jsonl"
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/import_public_dataset.py",
            "--adapter", "manifest", "--input", str(FIXTURE), "--output", str(output), "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["imported_rows"] == 1


def test_invalid_adapter_name_fails_clearly(tmp_path) -> None:
    result, code = import_public_dataset("unknown", FIXTURE, tmp_path / "draft.jsonl", ImportOptions())
    assert code == 1
    assert "unknown adapter" in result["errors"][0]


def test_duplicate_imported_ids_are_rejected(tmp_path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    rtl = tmp_path / "counter.v"
    rtl.write_text("module dup_counter(input logic clk, output logic q); always_ff @(posedge clk) q <= ~q; endmodule\n", encoding="utf-8")
    row = {
        "id": "dup", "source": "public_verilog_eval", "license": "fixture",
        "design_family": "counter", "task_type": "rtl_bug_review", "user_goal": "find_correctness_bug",
        "artifacts": {"rtl_code_path": "counter.v"},
        "provenance": {"public_dataset_name": "Fixture", "public_dataset_url": None, "source_commit": None, "notes": "test"},
    }
    manifest.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")
    result, code = import_public_dataset("manifest", manifest, tmp_path / "draft.jsonl", ImportOptions())
    assert code == 0
    assert result["imported_rows"] == 1
    assert result["rejected_examples"] == 1
    assert "duplicate output row id" in (tmp_path / "draft.rejected.jsonl").read_text(encoding="utf-8")


def test_verilog_eval_adapter_delegates_to_manifest() -> None:
    adapter = get_adapter("verilog_eval")
    result = adapter.discover_examples(FIXTURE.parent, ImportOptions())
    assert result.discovered_examples == 1
    assert len(result.examples) == 1
