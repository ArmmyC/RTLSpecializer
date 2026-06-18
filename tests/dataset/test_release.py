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


def _golden_config(name: str, output_root: Path) -> ReleaseConfig:
    return ReleaseConfig(name, output_root, [GOLDEN], seed=7, allow_source_overlap=True)


def _non_golden_copy(row: dict, row_id: str, family: str | None = None) -> dict:
    other = deepcopy(row)
    other["id"] = row_id
    other["source"] = "synthetic_rfid_style"
    if family is not None:
        other["design_family"] = family
    return other


def test_row_and_artifact_fingerprints_are_stable(valid_row) -> None:
    assert row_fingerprint(valid_row) == row_fingerprint(deepcopy(valid_row))
    assert artifact_fingerprint(valid_row) == artifact_fingerprint(deepcopy(valid_row))


def test_release_builder_accepts_golden_and_writes_layout(tmp_path) -> None:
    result, code = build_release(_golden_config("test_release", tmp_path / "releases"))
    assert code == 0, result
    release_dir = tmp_path / "releases" / "test_release"
    for name in ("train.jsonl", "val.jsonl", "test.jsonl", "rejected_rows.jsonl", "manifest.json", "stats.json", "dataset_card.md"):
        assert (release_dir / name).exists()
    assert result["accepted_rows"] == 20
    assert result["rejected_rows"] == 0
    assert result["train_rows"] + result["val_rows"] + result["test_rows"] == 20


def test_release_files_validate_under_strict(tmp_path) -> None:
    result, code = build_release(_golden_config("test_release", tmp_path / "releases"))
    assert code == 0, result
    release_dir = tmp_path / "releases" / "test_release"
    for split in ("train", "val", "test"):
        report = validate_dataset_file(release_dir / f"{split}.jsonl", strict=True)
        assert report.ok, [item.format() for item in report.errors + report.warnings]


def test_manifest_contains_hashes_and_row_counts(tmp_path) -> None:
    result, code = build_release(_golden_config("test_release", tmp_path / "releases"))
    assert code == 0, result
    manifest = json.loads((tmp_path / "releases" / "test_release" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["release_name"] == "test_release"
    assert manifest["files"]["train"]["sha256"]
    assert manifest["files"]["train"]["rows"] == result["train_rows"]
    assert manifest["files"]["rejected"]["rows"] == 0


def test_stats_contain_task_source_and_family_counts(tmp_path) -> None:
    result, code = build_release(_golden_config("test_release", tmp_path / "releases"))
    assert code == 0, result
    stats = json.loads((tmp_path / "releases" / "test_release" / "stats.json").read_text(encoding="utf-8"))
    assert stats["rows_by_source"]["handwritten_golden"] == 20
    assert stats["rows_by_task_type"]["rtl_bug_review"] == 5
    assert stats["rows_by_design_family"]["counter"] == 2
    assert stats["claim_levels"]["correctness"]["suggestion_only"] == 20
    leakage = stats["duplicate_leakage_checks"]
    assert "family_overlaps" in leakage
    assert "source_overlaps" in leakage
    assert "artifact_fingerprint_overlaps" in leakage


def test_duplicate_row_ids_are_rejected(tmp_path, valid_row) -> None:
    first = write_rows(tmp_path / "dup_a.jsonl", [valid_row])
    second = write_rows(tmp_path / "dup_b.jsonl", [deepcopy(valid_row)])
    result, code = build_release(ReleaseConfig("dup_release", tmp_path / "releases", [first, second], seed=7))
    assert code == 0, result
    assert result["accepted_rows"] == 1
    rejected = _rows(tmp_path / "releases" / "dup_release" / "rejected_rows.jsonl")
    assert rejected[0]["reason"] == "duplicate row id"
    stats = json.loads((tmp_path / "releases" / "dup_release" / "stats.json").read_text(encoding="utf-8"))
    assert stats["duplicate_leakage_checks"]["duplicate_row_ids"] == 1
    assert stats["duplicate_leakage_checks"]["duplicate_row_fingerprints"] == 1


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
    first, code_first = build_release(_golden_config("r1", tmp_path / "releases"))
    second, code_second = build_release(_golden_config("r2", tmp_path / "releases"))
    assert code_first == code_second == 0, (first, second)
    for split in ("train", "val", "test"):
        assert (tmp_path / "releases" / "r1" / f"{split}.jsonl").read_text(encoding="utf-8") == (tmp_path / "releases" / "r2" / f"{split}.jsonl").read_text(encoding="utf-8")


def test_design_family_overlap_is_prevented_by_default(tmp_path) -> None:
    result, code = build_release(_golden_config("test_release", tmp_path / "releases"))
    assert code == 0, result
    families: dict[str, str] = {}
    for split in ("train", "val", "test"):
        for row in _rows(tmp_path / "releases" / "test_release" / f"{split}.jsonl"):
            assert families.setdefault(row["design_family"], split) == split


def test_default_source_overlap_fails_for_golden(tmp_path) -> None:
    result, code = build_release(ReleaseConfig("source_fail", tmp_path / "releases", [GOLDEN], seed=7))
    assert code == 1
    assert any("source appears in multiple splits: handwritten_golden" in error for error in result["errors"])
    stats = json.loads((tmp_path / "releases" / "source_fail" / "stats.json").read_text(encoding="utf-8"))
    assert "handwritten_golden" in stats["duplicate_leakage_checks"]["source_overlaps"]


def test_source_overlap_passes_with_warning_when_allowed(tmp_path) -> None:
    result, code = build_release(_golden_config("source_allowed", tmp_path / "releases"))
    assert code == 0, result
    assert any("source overlap allowed for handwritten_golden" in warning for warning in result["warnings"])


def test_same_split_duplicate_artifact_is_reported_not_fatal(tmp_path, valid_row) -> None:
    other = _non_golden_copy(valid_row, valid_row["id"] + "_copy")
    path = write_rows(tmp_path / "same_split_artifact.jsonl", [valid_row, other])
    result, code = build_release(ReleaseConfig("artifact_same", tmp_path / "releases", [path], seed=7, allow_source_overlap=True))
    assert code == 0, result
    assert any("duplicate artifact fingerprint in one split" in warning for warning in result["warnings"])
    stats = json.loads((tmp_path / "releases" / "artifact_same" / "stats.json").read_text(encoding="utf-8"))
    overlaps = stats["duplicate_leakage_checks"]["artifact_fingerprint_overlaps"]
    assert len(overlaps) == 1
    assert len(next(iter(overlaps.values()))["splits"]) == 1


def test_cross_split_duplicate_artifact_fails_by_default(tmp_path, valid_row) -> None:
    other = _non_golden_copy(valid_row, valid_row["id"] + "_other_family", valid_row["design_family"] + "_other")
    path = write_rows(tmp_path / "cross_split_artifact.jsonl", [valid_row, other])
    result, code = build_release(ReleaseConfig("artifact_cross", tmp_path / "releases", [path], ratios=(.5, .5, 0), seed=7, allow_source_overlap=True))
    assert code == 1
    assert any("duplicate artifact fingerprint crosses splits" in error for error in result["errors"])


def test_cross_split_duplicate_artifact_passes_when_overlap_allowed(tmp_path, valid_row) -> None:
    other = _non_golden_copy(valid_row, valid_row["id"] + "_other_family", valid_row["design_family"] + "_other")
    path = write_rows(tmp_path / "cross_split_artifact.jsonl", [valid_row, other])
    result, code = build_release(ReleaseConfig("artifact_cross_allowed", tmp_path / "releases", [path], ratios=(.5, .5, 0), seed=7, allow_source_overlap=True, allow_family_overlap=True))
    assert code == 0, result
    assert any("artifact fingerprint overlap allowed" in warning for warning in result["warnings"])


def test_strict_input_validation_failure_makes_release_fail(tmp_path, valid_row) -> None:
    valid_row["messages"][2]["content"]["claim_levels"]["correctness"] = "verified"
    path = write_rows(tmp_path / "invalid.jsonl", [valid_row])
    result, code = build_release(ReleaseConfig("invalid_input", tmp_path / "releases", [path], seed=7))
    assert code == 1
    assert any("verified requires a passing simulation or equivalence check" in error for error in result["errors"])


def test_release_cli_json_output_is_parseable(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/build_dataset_release.py",
            "--release-name", "cli_release",
            "--input", str(GOLDEN),
            "--output-dir", str(tmp_path / "releases"),
            "--seed", "7",
            "--allow-source-overlap",
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
