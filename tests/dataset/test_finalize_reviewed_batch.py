from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import scripts.dataset.finalize_reviewed_batch as finalizer
from scripts.dataset.finalize_reviewed_batch import FinalizationConfig, finalize_batch


ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / "tests" / "fixtures" / "public_review" / "draft_rows.jsonl"
REVIEWED = ROOT / "tests" / "fixtures" / "public_review" / "reviewed_rows.jsonl"
GOLDEN = ROOT / "data" / "golden" / "golden_v0.1.jsonl"


def _batch(tmp_path: Path, ready: bool = True) -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    shutil.copyfile(DRAFT, batch / "selected_rows.jsonl")
    shutil.copyfile(REVIEWED if ready else DRAFT, batch / "reviewed_rows.jsonl")
    return batch


def _config(tmp_path: Path, batch: Path, *, force: bool = False) -> FinalizationConfig:
    return FinalizationConfig(
        batch_dir=batch,
        processed_output=tmp_path / "processed.jsonl",
        promotion_report=tmp_path / "promotion.json",
        release_name="fixture_release",
        release_output_dir=tmp_path / "releases",
        candidate_output=tmp_path / "candidates.jsonl",
        eval_output_dir=tmp_path / "eval",
        golden_input=GOLDEN,
        allow_source_overlap=True,
        force=force,
    )


def _directory_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")


def test_finalization_stops_for_unchanged_review(tmp_path) -> None:
    config = _config(tmp_path, _batch(tmp_path, ready=False))
    result = finalize_batch(config)
    assert result["ok"] is False
    assert result["readiness"]["all_rows_ready"] is False
    assert not config.processed_output.exists()
    assert not (config.release_output_dir / config.release_name).exists()


def test_finalization_promotes_ready_fixture(tmp_path) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    result = finalize_batch(config)
    assert result["ok"] is True, result
    promoted = json.loads(config.processed_output.read_text(encoding="utf-8").splitlines()[0])
    assert promoted["review_status"] == "validated"
    assert result["promotion"] == {"accepted_rows": 1, "rejected_rows": 0}


def test_strict_promotion_failure_stops_release(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    monkeypatch.setattr(finalizer, "promote_rows", lambda *args, **kwargs: ({
        "ok": False, "accepted_rows": 0, "rejected_rows": 1,
        "errors": ["strict mode rejects partial promotion"], "warnings": [],
    }, 1))
    result = finalize_batch(config)
    assert result["ok"] is False
    assert "strict promotion failed" in result["errors"][0]
    assert not (config.release_output_dir / config.release_name).exists()


def test_release_created_only_after_promotion_succeeds(tmp_path) -> None:
    failed_config = _config(tmp_path, _batch(tmp_path, ready=False))
    finalize_batch(failed_config)
    assert not (failed_config.release_output_dir / failed_config.release_name).exists()
    success_root = tmp_path / "success"
    success_root.mkdir()
    success_config = _config(success_root, _batch(success_root))
    assert finalize_batch(success_config)["ok"] is True
    assert (success_config.release_output_dir / success_config.release_name / "manifest.json").exists()


def test_candidate_created_only_after_release_succeeds(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    monkeypatch.setattr(finalizer, "build_release", lambda *args, **kwargs: ({
        "ok": False, "output_dir": str(config.release_output_dir / config.release_name),
        "accepted_rows": 0, "test_rows": 0, "errors": ["fixture release failure"], "warnings": [],
    }, 1))
    result = finalize_batch(config)
    assert result["ok"] is False
    assert not config.candidate_output.exists()


def test_evaluation_created_only_after_candidates_succeed(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    monkeypatch.setattr(finalizer, "make_candidates", lambda *args, **kwargs: ({
        "ok": False, "errors": ["fixture candidate failure"], "warnings": [],
    }, 1))
    result = finalize_batch(config)
    assert result["ok"] is False
    assert not config.eval_output_dir.exists()


def test_json_summary_is_written_and_parseable(tmp_path) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    result = finalize_batch(config)
    report = json.loads((config.batch_dir / "finalization_summary.json").read_text(encoding="utf-8"))
    assert report["ok"] is result["ok"] is True
    assert report["evaluation"]["rows_evaluated"] > 0


def test_markdown_summary_contains_local_only_warning(tmp_path) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    assert finalize_batch(config)["ok"] is True
    markdown = (config.batch_dir / "finalization_summary.md").read_text(encoding="utf-8")
    assert "Local-only notice" in markdown
    assert "does not replace human review" in markdown


def test_cli_json_output_is_parseable(tmp_path) -> None:
    batch = _batch(tmp_path)
    completed = subprocess.run([
        sys.executable, "scripts/dataset/finalize_reviewed_batch.py",
        "--batch-dir", str(batch),
        "--processed-output", str(tmp_path / "processed.jsonl"),
        "--promotion-report", str(tmp_path / "promotion.json"),
        "--release-name", "fixture_release",
        "--release-output-dir", str(tmp_path / "releases"),
        "--candidate-output", str(tmp_path / "candidates.jsonl"),
        "--eval-output-dir", str(tmp_path / "eval"),
        "--golden-input", str(GOLDEN), "--allow-source-overlap", "--json",
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert json.loads(completed.stdout)["ok"] is True


def test_outputs_stay_within_requested_paths(tmp_path) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    assert finalize_batch(config)["ok"] is True
    expected = {
        "batch", "processed.jsonl", "processed.rejected.jsonl", "promotion.json",
        "releases", "candidates.jsonl", "eval",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected


def test_existing_outputs_are_protected_without_force(tmp_path) -> None:
    config = _config(tmp_path, _batch(tmp_path))
    config.processed_output.write_text("keep me\n", encoding="utf-8")
    result = finalize_batch(config)
    assert result["ok"] is False
    assert "output already exists" in " ".join(result["errors"])
    assert config.processed_output.read_text(encoding="utf-8") == "keep me\n"


def test_force_replaces_only_managed_outputs(tmp_path) -> None:
    batch = _batch(tmp_path)
    config = _config(tmp_path, batch)
    assert finalize_batch(config)["ok"] is True
    unknown_batch_file = batch / "reviewer_notes.md"
    unknown_parent_file = config.release_output_dir / "keep.txt"
    unknown_batch_file.write_text("keep\n", encoding="utf-8")
    unknown_parent_file.write_text("keep\n", encoding="utf-8")
    forced = _config(tmp_path, batch, force=True)
    assert finalize_batch(forced)["ok"] is True
    assert unknown_batch_file.read_text(encoding="utf-8") == "keep\n"
    assert unknown_parent_file.read_text(encoding="utf-8") == "keep\n"


@pytest.mark.parametrize("output_name", ["release_dir", "eval_dir"])
def test_force_rejects_symlinked_managed_output_directory(tmp_path, output_name) -> None:
    batch = _batch(tmp_path)
    config = _config(tmp_path, batch, force=True)
    target = tmp_path / f"{output_name}_target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    output = (
        config.release_output_dir / config.release_name
        if output_name == "release_dir"
        else config.eval_output_dir
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _directory_symlink(output, target)

    result = finalize_batch(config)

    assert result["ok"] is False
    assert "managed output directory must not be a symlink" in " ".join(result["errors"])
    assert output.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


@pytest.mark.parametrize("dangerous", [Path.home(), ROOT, Path(ROOT.anchor)])
def test_managed_output_directory_rejects_dangerous_roots(tmp_path, dangerous) -> None:
    config = replace(_config(tmp_path, _batch(tmp_path), force=True), eval_output_dir=dangerous)

    result = finalize_batch(config)

    assert result["ok"] is False
    assert "managed output directory must not be" in " ".join(result["errors"])


def test_managed_output_directory_cannot_contain_batch_inputs(tmp_path) -> None:
    release_root = tmp_path / "releases"
    batch_parent = release_root / "fixture_release"
    batch_parent.mkdir(parents=True)
    config = _config(tmp_path, _batch(batch_parent), force=True)

    result = finalize_batch(config)

    assert result["ok"] is False
    assert "release_dir must not contain an input path" in " ".join(result["errors"])


def test_cli_json_preflight_failure_is_parseable(tmp_path) -> None:
    missing_batch = tmp_path / "missing"
    completed = subprocess.run([
        sys.executable, "scripts/dataset/finalize_reviewed_batch.py",
        "--batch-dir", str(missing_batch),
        "--processed-output", str(tmp_path / "processed.jsonl"),
        "--promotion-report", str(tmp_path / "promotion.json"),
        "--release-name", "fixture_release",
        "--release-output-dir", str(tmp_path / "releases"),
        "--candidate-output", str(tmp_path / "candidates.jsonl"),
        "--eval-output-dir", str(tmp_path / "eval"),
        "--golden-input", str(GOLDEN), "--json", "--force",
    ], cwd=ROOT, text=True, capture_output=True, check=False)

    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["ok"] is False
    assert any("batch directory not found" in error for error in payload["errors"])


def test_local_data_output_is_rejected(tmp_path) -> None:
    batch = _batch(tmp_path)
    local_data = tmp_path / ".local_data"
    config = FinalizationConfig(
        batch_dir=batch,
        processed_output=local_data / "processed.jsonl",
        promotion_report=tmp_path / "promotion.json",
        release_name="fixture_release",
        release_output_dir=tmp_path / "releases",
        candidate_output=tmp_path / "candidates.jsonl",
        eval_output_dir=tmp_path / "eval",
        golden_input=GOLDEN,
    )
    result = finalize_batch(config)
    assert result["ok"] is False
    assert ".local_data" in " ".join(result["errors"])
    assert not local_data.exists()
