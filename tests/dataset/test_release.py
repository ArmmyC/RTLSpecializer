from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.release import ReleaseConfig, artifact_fingerprint, build_release, row_fingerprint
from scripts.dataset.validation import validate_dataset_file
from tests.dataset.conftest import GOLDEN, ROOT, write_rows


def _rows(path: Path) -> list[dict]:
    loaded, problems = load_jsonl(path)
    assert not problems
    return [row for _, row in loaded]


def test_row_and_artifact_fingerprints_are_stable(valid_row) -> None:
    assert row_fingerprint(valid_row) == row_fingerprint(deepcopy(valid_row))
    assert artifact_fingerprint(valid_row) == artifact_fingerprint(deepcopy(valid_row))


def test_release_builder_accepts_golden_and_writes_layout(tmp_path) -> None:
    result, code = build_release(ReleaseConfig("test_release", tmp_path / "releases", [GOLDEN], seed=7))
    assert code == 0, result
    release_dir = tmp_path / "releases" / "test_release"
    for name in ("train.jsonl", "val.jsonl", "test.jsonl", "rejected_rows.jsonl", "manifest.json", "stats.json", "dataset_card.md"):
        assert (release_dir / name).exists()
    assert result["accepted_rows"] == 20
    assert result["rejected_rows"] == 0
    assert result["train_rows"] + result["val_rows"] + result["test_rows"] == 20


def test_release_files_validate_under_strict(tmp_path) -> None:
    result, code = build_release(ReleaseConfig("test_release", tmp_path / "releases", [GOLDEN], seed=7))
    assert code == 0, result
    release_dir = tmp_path / "releases" / "test_release"
    for split in ("train", "val", "test"):
        report = validate_dataset_file(release_dir / f"{split}.jsonl", strict=True)
        assert report.ok, [item.format() for item in report.errors + report.warnings]


def test_manifest_contains_hashes_and_row_counts(tmp_path) -> None:
    result, code = build_release(ReleaseConfig("test_release", tmp_path / "releases", [GOLDEN], seed=7))
    assert code == 0, result
    manifest = json.loads((tmp_path / "releases" / "test_release" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["release_name"] == "test_release"
    assert manifest["files"]["train"]["sha256"]
    assert manifest["files"]["train"]["rows"] == result["train_rows"]
    assert manifest["files"]["rejected"]["rows"] == 0


def test_stats_contain_task_source_and_family_counts(tmp_path) -> None:
    result, code = build_release(ReleaseConfig("test_release", tmp_path / "releases", [GOLDEN], seed=7))
    assert code == 0, result
    stats = json.loads((tmp_path / "releases" / "test_release" / "stats.json").read_text(encoding="utf-8"))
    assert stats["rows_by_source"]["handwritten_golden"] == 20
    assert stats["rows_by_task_type"]["rtl_bug_review"] == 5
    assert stats["rows_by_design_family"]["counter"] == 2
    assert stats["claim_levels"]["correctness"]["suggestion_only"] == 20


def test_duplicate_row_ids_are_rejected(tmp_path, valid_row) -> None:
    rows = [valid_row, deepcopy(valid_row)]
    path = write_rows(tmp_path / "dup.jsonl", rows)
    result, code = build_release(ReleaseConfig("dup_release", tmp_path / "releases", [path], seed=7))
    assert code == 1
    assert result["accepted_rows"] == 1
    rejected = _rows(tmp_path / "releases" / "dup_release" / "rejected_rows.jsonl")
    assert rejected[0]["reason"] == "duplicate row id"


def test_draft_rows_are_rejected(tmp_path, valid_row) -> None:
    valid_row["review_status"] = "draft"
    path = write_rows(tmp_path / "draft.jsonl", [valid_row])
    result, code = build_release(ReleaseConfig("draft_release", tmp_path / "releases", [path], seed=7))
    assert code == 1
    assert result["accepted_rows"] == 0
    rejected = _rows(tmp_path / "releases" / "draft_release" / "rejected_rows.jsonl")
    assert rejected[0]["reason"] == "review status gate"


def test_unknown_license_is_rejected(tmp_path, valid_row) -> None:
    valid_row["license"] = "unknown"
    path = write_rows(tmp_path / "unknown.jsonl", [valid_row])
    result, code = build_release(ReleaseConfig("license_release", tmp_path / "releases", [path], seed=7))
    assert code == 1
    rejected = _rows(tmp_path / "releases" / "license_release" / "rejected_rows.jsonl")
    assert rejected[0]["reason"] == "license gate"


def test_repeated_run_with_same_seed_is_deterministic(tmp_path) -> None:
    first, code_first = build_release(ReleaseConfig("r1", tmp_path / "releases", [GOLDEN], seed=7))
    second, code_second = build_release(ReleaseConfig("r2", tmp_path / "releases", [GOLDEN], seed=7))
    assert code_first == code_second == 0, (first, second)
    for split in ("train", "val", "test"):
        assert (tmp_path / "releases" / "r1" / f"{split}.jsonl").read_text(encoding="utf-8") == (tmp_path / "releases" / "r2" / f"{split}.jsonl").read_text(encoding="utf-8")


def test_design_family_overlap_is_prevented_by_default(tmp_path) -> None:
    result, code = build_release(ReleaseConfig("test_release", tmp_path / "releases", [GOLDEN], seed=7))
    assert code == 0, result
    families: dict[str, str] = {}
    for split in ("train", "val", "test"):
        for row in _rows(tmp_path / "releases" / "test_release" / f"{split}.jsonl"):
            assert families.setdefault(row["design_family"], split) == split


def test_release_cli_json_output_is_parseable(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/build_dataset_release.py",
            "--release-name", "cli_release",
            "--input", str(GOLDEN),
            "--output-dir", str(tmp_path / "releases"),
            "--seed", "7",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["accepted_rows"] == 20
