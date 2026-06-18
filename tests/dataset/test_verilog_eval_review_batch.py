from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.prepare_verilog_eval_review_batch import prepare_batch
from scripts.dataset.validation import validate_dataset_file
from tests.dataset.conftest import ROOT


FIXTURE = ROOT / "tests" / "fixtures" / "verilog_eval_review" / "manifest.jsonl"


def _rows(path: Path) -> list[dict]:
    loaded, problems = load_jsonl(path)
    assert not problems
    return [row for _, row in loaded]


def test_prepares_review_batch_from_manifest_fixture(tmp_path) -> None:
    result, code = prepare_batch(FIXTURE, tmp_path / "batch", 3, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 0, result
    assert result["draft_rows"] == 3
    assert result["selected_rows"] == 3
    assert (tmp_path / "batch" / "review_packet" / "README.md").exists()
    assert (tmp_path / "batch" / "review_packet" / "review_manifest.jsonl").exists()
    assert (tmp_path / "batch" / "reviewed_rows.jsonl").exists()
    assert validate_dataset_file(tmp_path / "batch" / "selected_rows.jsonl", strict=True).ok


def test_selected_rows_are_draft_unsplit_public_source(tmp_path) -> None:
    result, code = prepare_batch(FIXTURE, tmp_path / "batch", 2, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 0, result
    rows = _rows(tmp_path / "batch" / "selected_rows.jsonl")
    assert len(rows) == 2
    for row in rows:
        assert row["review_status"] == "draft"
        assert row["split"] == "unsplit"
        assert row["created_by"] == "script"
        assert row["source"] == "public_verilog_eval"


def test_selection_is_deterministic(tmp_path) -> None:
    first, code_first = prepare_batch(FIXTURE, tmp_path / "a", 2, "public_verilog_eval", "fixture_public_safe", set())
    second, code_second = prepare_batch(FIXTURE, tmp_path / "b", 2, "public_verilog_eval", "fixture_public_safe", set())
    assert code_first == code_second == 0, (first, second)
    assert (tmp_path / "a" / "selected_rows.jsonl").read_text(encoding="utf-8") == (tmp_path / "b" / "selected_rows.jsonl").read_text(encoding="utf-8")


def test_invalid_missing_artifact_is_rejected(tmp_path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "id": "missing", "source": "public_verilog_eval", "license": "fixture_public_safe",
            "design_family": "counter", "task_type": "rtl_bug_review", "user_goal": "find_correctness_bug",
            "artifacts": {"rtl_code_path": "missing.sv"},
            "provenance": {"public_dataset_name": "Fixture", "public_dataset_url": None, "source_commit": None, "notes": "test"},
        }) + "\n",
        encoding="utf-8",
    )
    result, code = prepare_batch(manifest, tmp_path / "batch", 3, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 1
    assert result["rejected_rows"] == 1
    assert "no rows selected" in result["errors"]


def test_limit_is_respected(tmp_path) -> None:
    result, code = prepare_batch(FIXTURE, tmp_path / "batch", 1, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 0, result
    assert result["selected_rows"] == 1


def test_cli_json_output_is_parseable(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/prepare_verilog_eval_review_batch.py",
            "--input", str(FIXTURE),
            "--output-dir", str(tmp_path / "batch"),
            "--limit", "3",
            "--license", "fixture_public_safe",
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
    assert payload["selected_rows"] == 3


def test_seed_provenance_docs_mention_synthetic_seed_smoke_status() -> None:
    docs = (ROOT / "docs" / "dataset" / "dataset_guidelines.md").read_text(encoding="utf-8").lower()
    workflow = (ROOT / "docs" / "dataset" / "verilog_eval_review_workflow.md").read_text(encoding="utf-8").lower()
    assert "synthetic seed" in docs
    assert "smoke" in docs
    assert "synthetic seed" in workflow
