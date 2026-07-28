from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

import scripts.dataset.data_workspace_layout as workspace_layout
from scripts.dataset.data_workspace_layout import (
    WorkspaceError,
    build_migration_plan,
    initialize_manual_rtl_run,
    migrate_legacy_rtl_data_workspace,
)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "data_workspace_layout" / "legacy"


def _copy_fixture(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    return data


def _file_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, str | None], ...]:
    snapshot: list[tuple[str, str, str | None]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            snapshot.append((relative, "directory", None))
        elif path.is_file():
            snapshot.append((relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            snapshot.append((relative, "special", None))
    return tuple(snapshot)


EXPECTED_MAPPED_FILES = (
    (".local_data/verilog-eval-main/LICENSE", "raw/verilog_eval/upstream/LICENSE"),
    (".local_data/verilog-eval-main/README.md", "raw/verilog_eval/upstream/README.md"),
    (".local_data/verilog-eval-main/dataset_spec-to-rtl/Prob001_demo_prompt.txt", "raw/verilog_eval/upstream/dataset_spec-to-rtl/Prob001_demo_prompt.txt"),
    (".local_data/verilog-eval-main/dataset_spec-to-rtl/Prob001_demo_ref.sv", "raw/verilog_eval/upstream/dataset_spec-to-rtl/Prob001_demo_ref.sv"),
    (".local_data/verilog-eval-main/dataset_spec-to-rtl/Prob001_demo_test.sv", "raw/verilog_eval/upstream/dataset_spec-to-rtl/Prob001_demo_test.sv"),
    ("review/rtl_generation_normalization_batches/batch_001.json", "runs/manual_rtl_teacher/pilot_001/normalization/packets/batch_001.json"),
    (".local_data/manual_task_normalization/batch_001_response.json", "runs/manual_rtl_teacher/pilot_001/normalization/responses/batch_001_response.json"),
    (".local_data/rtl_generation_verification_assets/verification_assets.jsonl", "runs/manual_rtl_teacher/pilot_001/private_assets/verification_assets.jsonl"),
    ("review/rtl_generation_pilot/generation_tasks.jsonl", "runs/manual_rtl_teacher/pilot_001/tasks/generation_tasks.jsonl"),
    ("review/rtl_generation_pilot/verification_assets.jsonl", "runs/manual_rtl_teacher/pilot_001/tasks/verification_assets.jsonl"),
    ("review/rtl_teacher_generation_packets/packet_0001.json", "runs/manual_rtl_teacher/pilot_001/teacher/packets/packet_0001.json"),
    (".local_data/manual_teacher_responses/packet_0001_response.json", "runs/manual_rtl_teacher/pilot_001/teacher/responses/packet_0001_response.json"),
    ("review/rtl_generation_pilot/candidate_records.jsonl", "runs/manual_rtl_teacher/pilot_001/teacher/candidate_records.jsonl"),
    (".local_data/rtl_candidate_verification/run_001/verification_plan.jsonl", "runs/manual_rtl_teacher/pilot_001/verification/attempt_01/verification_plan.jsonl"),
    ("review/rtl_generation_pilot/generation_attempts.jsonl", "runs/manual_rtl_teacher/pilot_001/verification/generation_attempts.jsonl"),
    ("review/rtl_teacher_repair_packets/attempt_02/packet_0001.json", "runs/manual_rtl_teacher/pilot_001/repairs/attempt_02/packet_0001.json"),
)


def _add_forbidden_source_files(data: Path) -> None:
    for relative in (
        ".local_data/verilog-eval-main/dataset_spec-to-rtl/.git/config",
        ".local_data/verilog-eval-main/dataset_spec-to-rtl/.cache/index",
        ".local_data/verilog-eval-main/dataset_spec-to-rtl/credentials.json",
        ".local_data/verilog-eval-main/dataset_spec-to-rtl/scratch.tmp",
    ):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not migrate\n", encoding="utf-8")


def _assert_no_forbidden_destination_entries(data: Path) -> None:
    forbidden_names = {".git", ".cache", "credentials", "credentials.json"}
    for root in (data / "raw", data / "runs"):
        for path in root.rglob("*"):
            assert not any(part.casefold() in forbidden_names for part in path.parts)
            assert not path.name.casefold().endswith((".tmp", ".temp", ".swp", ".swo", "~"))


def test_dry_run_is_deterministic_and_does_not_copy_sources(tmp_path: Path) -> None:
    data = _copy_fixture(tmp_path)
    before = _file_hashes(data)
    plan = build_migration_plan(data, "pilot_001")
    assert plan["mode"] == "dry_run"
    assert plan["summary"]["mapping_count"] == 14
    assert plan["collisions"] == []
    assert plan["migration_id"] == build_migration_plan(data, "pilot_001")["migration_id"]
    assert not (data / "raw" / "verilog_eval").exists()
    assert _file_hashes(data) == before
    assert all("module demo" not in json.dumps(plan) for _ in [0])


def test_apply_is_copy_only_and_every_mapped_file_hash_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _copy_fixture(tmp_path)
    _add_forbidden_source_files(data)
    before = _file_hashes(data)
    dry_run = build_migration_plan(data, "pilot_001")
    initialize_manual_rtl_run("pilot_001", "VerilogEval", data / "runs/manual_rtl_teacher")
    report = migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)
    assert report["mode"] == "apply"
    assert report["collisions"] == []
    assert report["summary"]["mapping_count"] == 14
    assert dry_run["summary"]["planned_file_count"] == len(EXPECTED_MAPPED_FILES)
    after = _file_hashes(data)
    for path, digest in before.items():
        assert after[path] == digest
    for source_relative, destination_relative in EXPECTED_MAPPED_FILES:
        source = data / source_relative
        destination = data / destination_relative
        assert destination.is_file(), destination
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == before[source_relative]
    _assert_no_forbidden_destination_entries(data)
    assert all(path.stat().st_mode & 0o111 == 0 for path in (data / "raw").rglob("*") if path.is_file())

    destination_hashes = {
        destination_relative: hashlib.sha256((data / destination_relative).read_bytes()).hexdigest()
        for _, destination_relative in EXPECTED_MAPPED_FILES
    }
    second_dry_run = migrate_legacy_rtl_data_workspace(data, "pilot_001")
    assert second_dry_run["collisions"] == []
    assert all(item["status"] == "already_present" for item in second_dry_run["mappings"])

    def fail_if_copy_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("second apply rewrote a destination file")

    monkeypatch.setattr(workspace_layout, "_copy_stream", fail_if_copy_called)
    second_apply = migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)
    assert all(item["status"] == "already_present" for item in second_apply["mappings"])
    assert {
        destination_relative: hashlib.sha256((data / destination_relative).read_bytes()).hexdigest()
        for _, destination_relative in EXPECTED_MAPPED_FILES
    } == destination_hashes


def test_apply_requires_an_initialized_canonical_run(tmp_path: Path) -> None:
    data = _copy_fixture(tmp_path)
    report_path = data / "reports" / "migration" / "applied.json"
    with pytest.raises(WorkspaceError, match="initialized canonical run"):
        migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True, output=report_path)
    assert not (data / "raw").exists()
    assert not report_path.exists()


@pytest.mark.parametrize("mutation", ["malformed_manifest", "invalid_structure", "mismatched_run_id"])
def test_apply_rejects_invalid_canonical_run_before_copy(tmp_path: Path, mutation: str) -> None:
    data = _copy_fixture(tmp_path)
    initialize_manual_rtl_run("pilot_001", "VerilogEval", data / "runs/manual_rtl_teacher")
    run = data / "runs/manual_rtl_teacher/pilot_001"
    manifest_path = run / "run_manifest.json"
    if mutation == "malformed_manifest":
        manifest_path.write_text("{not-json\n", encoding="utf-8")
    elif mutation == "invalid_structure":
        (run / "review").rmdir()
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["run_id"] = "other_run"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WorkspaceError, match="initialized canonical run"):
        migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)
    assert not (data / "raw").exists()


def test_apply_rolls_back_after_staging_publication_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _copy_fixture(tmp_path)
    initialize_manual_rtl_run("pilot_001", "VerilogEval", data / "runs/manual_rtl_teacher")
    before = _tree_snapshot(data)
    report_path = tmp_path / "applied.json"
    original_replace = os.replace
    publication_count = 0

    def fail_on_second_publication(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal publication_count
        source_path = Path(source)
        if ".migration-" in source_path.name and "migration-backup" not in source_path.name:
            publication_count += 1
            if publication_count == 2:
                raise OSError("injected publication failure")
        original_replace(source, destination)

    monkeypatch.setattr(workspace_layout.os, "replace", fail_on_second_publication)
    with pytest.raises(OSError, match="injected publication failure"):
        migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True, output=report_path)

    assert publication_count == 2
    assert _tree_snapshot(data) == before
    assert not report_path.exists()
    assert not (data / "raw").exists()
    assert not any(".migration-" in path.name for path in data.rglob("*"))


def test_post_publish_validation_failure_uses_the_same_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = _copy_fixture(tmp_path)
    initialize_manual_rtl_run("pilot_001", "VerilogEval", data / "runs/manual_rtl_teacher")
    before = _tree_snapshot(data)
    report_path = tmp_path / "post-validation-failure.json"
    original_validate = workspace_layout._require_valid_canonical_run
    validation_count = 0

    def fail_post_publish_validation(data_root: Path, run_id: str) -> None:
        nonlocal validation_count
        validation_count += 1
        if validation_count == 2:
            raise WorkspaceError("injected post-copy validation failure")
        original_validate(data_root, run_id)

    monkeypatch.setattr(workspace_layout, "_require_valid_canonical_run", fail_post_publish_validation)
    with pytest.raises(WorkspaceError, match="post-copy validation"):
        migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True, output=report_path)

    assert validation_count == 2
    assert _tree_snapshot(data) == before
    assert not report_path.exists()
    assert not (data / "raw").exists()
    assert not any(".migration-" in path.name for path in data.rglob("*"))


def test_migration_rejects_conflicting_destination_and_source_symlink(tmp_path: Path) -> None:
    data = _copy_fixture(tmp_path)
    destination = data / "raw/verilog_eval/upstream/dataset_spec-to-rtl"
    destination.mkdir(parents=True)
    (destination / "Prob001_demo_ref.sv").write_text("different\n", encoding="utf-8")
    plan = build_migration_plan(data, "pilot_001")
    assert plan["collisions"]
    initialize_manual_rtl_run("pilot_001", "VerilogEval", data / "runs/manual_rtl_teacher")
    with pytest.raises(WorkspaceError):
        migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)

    data2 = _copy_fixture(tmp_path / "second")
    (data2 / ".local_data/verilog-eval-main/dataset_spec-to-rtl/link.sv").symlink_to(
        data2 / ".local_data/verilog-eval-main/dataset_spec-to-rtl/Prob001_demo_ref.sv"
    )
    with pytest.raises(WorkspaceError, match="symlink"):
        build_migration_plan(data2, "pilot_001")
