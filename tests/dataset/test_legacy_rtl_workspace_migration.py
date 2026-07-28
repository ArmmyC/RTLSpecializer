from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts.dataset.data_workspace_layout import (
    WorkspaceError,
    build_migration_plan,
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


def test_apply_is_copy_only_and_hashes_match(tmp_path: Path) -> None:
    data = _copy_fixture(tmp_path)
    before = _file_hashes(data)
    report = migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)
    assert report["mode"] == "apply"
    after = _file_hashes(data)
    for path, digest in before.items():
        assert after[path] == digest
    assert (data / "raw/verilog_eval/upstream/dataset_spec-to-rtl/Prob001_demo_ref.sv").read_text(encoding="utf-8").startswith("module demo")
    assert (data / "runs/manual_rtl_teacher/pilot_001/tasks/generation_tasks.jsonl").exists()
    assert not (data / "raw/verilog_eval/upstream/dataset_spec-to-rtl/.git").exists()
    assert all(path.stat().st_mode & 0o111 == 0 for path in (data / "raw/verilog_eval/upstream").rglob("*") if path.is_file())


def test_migration_rejects_conflicting_destination_and_source_symlink(tmp_path: Path) -> None:
    data = _copy_fixture(tmp_path)
    destination = data / "raw/verilog_eval/upstream/dataset_spec-to-rtl"
    destination.mkdir(parents=True)
    (destination / "Prob001_demo_ref.sv").write_text("different\n", encoding="utf-8")
    plan = build_migration_plan(data, "pilot_001")
    assert plan["collisions"]
    with pytest.raises(WorkspaceError):
        migrate_legacy_rtl_data_workspace(data, "pilot_001", apply=True)

    data2 = _copy_fixture(tmp_path / "second")
    (data2 / ".local_data/verilog-eval-main/dataset_spec-to-rtl/link.sv").symlink_to(
        data2 / ".local_data/verilog-eval-main/dataset_spec-to-rtl/Prob001_demo_ref.sv"
    )
    with pytest.raises(WorkspaceError, match="symlink"):
        build_migration_plan(data2, "pilot_001")
