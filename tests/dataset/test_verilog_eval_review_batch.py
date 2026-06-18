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
DIRECTORY_FIXTURE = ROOT / "tests" / "fixtures" / "verilog_eval_review" / "local_checkout"
JSONL_FIXTURE = ROOT / "tests" / "fixtures" / "verilog_eval_review" / "verilog_eval_export.jsonl"


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
    assert payload["input"] == str(FIXTURE)
    assert payload["source"] == "public_verilog_eval"
    assert payload["license"] == "fixture_public_safe"
    assert payload["rejected_rows_path"].endswith("rejected_rows.jsonl")


def test_nonempty_output_requires_force_and_force_is_scoped(tmp_path) -> None:
    output = tmp_path / "batch"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    failed, failed_code = prepare_batch(FIXTURE, output, 1, "public_verilog_eval", "fixture_public_safe", set())
    assert failed_code == 1
    assert "non-empty" in failed["errors"][0]

    result, code = prepare_batch(FIXTURE, output, 1, "public_verilog_eval", "fixture_public_safe", set(), force=True)
    assert code == 0, result
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert outside.read_text(encoding="utf-8") == "outside"


def test_force_replaces_stale_generated_packet(tmp_path) -> None:
    output = tmp_path / "batch"
    first, first_code = prepare_batch(FIXTURE, output, 3, "public_verilog_eval", "fixture_public_safe", set())
    assert first_code == 0, first
    stale = output / "review_packet" / "rows" / "stale.review.md"
    stale.write_text("stale", encoding="utf-8")
    second, second_code = prepare_batch(FIXTURE, output, 1, "public_verilog_eval", "fixture_public_safe", set(), force=True)
    assert second_code == 0, second
    assert not stale.exists()


def test_output_cannot_overlap_input_tree(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    result, code = prepare_batch(source, source / "batch", 1, "public_verilog_eval", "fixture_public_safe", set(), force=True)
    assert code == 1
    assert any("separate from the input tree" in error for error in result["errors"])
    assert not (source / "batch").exists()


def test_duplicate_generated_ids_reject_later_sorted_row(tmp_path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    fixture_root = FIXTURE.parent
    base = {
        "source": "public_verilog_eval", "license": "fixture_public_safe",
        "design_family": "counter", "task_type": "rtl_bug_review", "user_goal": "find_correctness_bug",
        "artifacts": {"rtl_code_path": str(fixture_root / "counter_ref.sv")},
        "provenance": {"public_dataset_name": "Synthetic duplicate fixture", "public_dataset_url": None, "source_commit": None, "notes": "test"},
    }
    manifest.write_text("\n".join(json.dumps({**base, "id": row_id}) for row_id in ("dup-a", "dup_a")) + "\n", encoding="utf-8")
    # Absolute paths are intentionally rejected by the manifest adapter, so copy the artifact beside it.
    (tmp_path / "counter_ref.sv").write_text((fixture_root / "counter_ref.sv").read_text(encoding="utf-8"), encoding="utf-8")
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["artifacts"]["rtl_code_path"] = "counter_ref.sv"
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result, code = prepare_batch(manifest, tmp_path / "batch", 3, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 0, result
    assert result["draft_rows"] == 1
    rejected = _rows(tmp_path / "batch" / "rejected_rows.jsonl")
    assert rejected[0]["source_id"] == "dup_a"
    assert rejected[0]["row_id"] == "public_verilog_eval_dup_a"
    assert rejected[0]["reason"] == "duplicate output row id"


def test_invalid_source_fails_before_discovery(tmp_path) -> None:
    missing = tmp_path / "missing"
    result, code = prepare_batch(missing, tmp_path / "batch", 1, "teacher_generated", "fixture_public_safe", set())
    assert code == 1
    assert any("invalid --source" in error and "public_verilog_eval" in error for error in result["errors"])
    assert not (tmp_path / "batch").exists()


def test_local_directory_fixture_and_prompt_rendering(tmp_path) -> None:
    result, code = prepare_batch(DIRECTORY_FIXTURE, tmp_path / "batch", 2, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 0, result
    assert result["selected_rows"] == 2
    markdown = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "batch" / "review_packet" / "rows").glob("*.review.md"))
    assert "### VerilogEval prompt/specification" in markdown
    prompt_block = markdown.split("### VerilogEval prompt/specification", 1)[1].split("```", 2)
    assert prompt_block[1].startswith("text\n")
    assert "systemverilog" not in prompt_block[1]
    assert validate_dataset_file(tmp_path / "batch" / "draft_rows.jsonl", strict=True).ok
    assert validate_dataset_file(tmp_path / "batch" / "reviewed_rows.jsonl", strict=True).ok


def test_local_jsonl_fixture_rejections_limit_and_audit_report(tmp_path) -> None:
    result, code = prepare_batch(JSONL_FIXTURE, tmp_path / "batch", 10, "public_verilog_eval", "fixture_public_safe", set())
    assert code == 0, result
    assert result["draft_rows"] == 2
    assert result["rejected_rows"] == 2
    rejected = _rows(tmp_path / "batch" / "rejected_rows.jsonl")
    assert all(item["reason"] == "missing prompt/spec or RTL" for item in rejected)
    report = json.loads((tmp_path / "batch" / "selection_report.json").read_text(encoding="utf-8"))
    assert report["source"] == "public_verilog_eval"
    assert report["license"] == "fixture_public_safe"
    assert report["selection"][0]["selection_score"] > 0
    assert report["selection"][0]["score_reasons"]

    limited, limited_code = prepare_batch(JSONL_FIXTURE, tmp_path / "limited", 1, "public_verilog_eval", "fixture_public_safe", set())
    assert limited_code == 0, limited
    assert limited["selected_rows"] == 1


def test_seed_provenance_docs_mention_synthetic_seed_smoke_status() -> None:
    docs = (ROOT / "docs" / "dataset" / "dataset_guidelines.md").read_text(encoding="utf-8").lower()
    workflow = (ROOT / "docs" / "dataset" / "verilog_eval_review_workflow.md").read_text(encoding="utf-8").lower()
    assert "synthetic seed" in docs
    assert "smoke" in docs
    assert "synthetic seed" in workflow
