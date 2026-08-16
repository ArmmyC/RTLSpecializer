from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dataset.data_workspace_layout import (
    CANONICAL_RUN_PATHS,
    WorkspaceError,
    initialize_manual_rtl_run,
    validate_manual_rtl_run,
)


def test_init_creates_exact_deterministic_run_and_resume(tmp_path: Path) -> None:
    runs_root = tmp_path / "data" / "runs" / "manual_rtl_teacher"
    result = initialize_manual_rtl_run("pilot_001", "VerilogEval", runs_root)
    assert result["status"] == "created"
    run = runs_root / "pilot_001"
    expected_dirs = {path for path in (run / relative for relative in {
        "normalization", "teacher",
        "normalization/packets", "normalization/responses", "tasks", "private_assets",
        "teacher/packets", "teacher/responses", "verification", "repairs", "review", "reports",
    })}
    assert {path for path in run.rglob("*") if path.is_dir()} == expected_dirs
    manifest_bytes = (run / "run_manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert manifest["paths"] == CANONICAL_RUN_PATHS
    assert "/" not in manifest["run_id"]
    report, code = validate_manual_rtl_run(run)
    assert code == 0, report
    resumed = initialize_manual_rtl_run("pilot_001", "VerilogEval", runs_root, resume=True)
    assert resumed["status"] == "resumed"
    assert (run / "run_manifest.json").read_bytes() == manifest_bytes

    with pytest.raises(WorkspaceError, match="source_dataset"):
        initialize_manual_rtl_run("pilot_001", "RTLCoder", runs_root, resume=True)


def test_resume_rejects_an_altered_manifest(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", runs_root)
    manifest_path = runs_root / "pilot_001" / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["max_attempts"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WorkspaceError, match="exact manifest and structure"):
        initialize_manual_rtl_run("pilot_001", "VerilogEval", runs_root, resume=True)


@pytest.mark.parametrize("run_id", ["Pilot_001", "pilot-001", "../escape", "pilot/001"])
def test_init_rejects_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(WorkspaceError):
        initialize_manual_rtl_run(run_id, "VerilogEval", tmp_path / "runs")


def test_run_validation_enforces_private_boundary_and_attempt_folders(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "pilot_001"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", run.parent)
    (run / "private_assets" / "workspace").mkdir()
    (run / "private_assets" / "workspace" / "reference.sv").write_text("module ref; endmodule\n", encoding="utf-8")
    (run / "private_assets" / "workspace" / "testbench.sv").write_text("module tb; endmodule\n", encoding="utf-8")
    (run / "verification" / "attempt_01").mkdir()
    valid, code = validate_manual_rtl_run(run)
    assert code == 0, valid

    (run / "teacher" / "packets" / "reference.sv").write_text("module ref; endmodule\n", encoding="utf-8")
    invalid, code = validate_manual_rtl_run(run)
    assert code == 1
    assert any("private RTL" in error for error in invalid["errors"])


def test_run_validation_accepts_nested_verification_workspace(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "pilot_001"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", run.parent)

    attempt = run / "verification" / "attempt_01"
    attempt.mkdir()
    (attempt / "candidate_manifest.jsonl").write_text("{}\n", encoding="utf-8")
    (attempt / "verification_plan.jsonl").write_text("{}\n", encoding="utf-8")
    (attempt / "run_instructions.md").write_text("manual handoff\n", encoding="utf-8")
    workspace = attempt / "workspace" / "rtlgen_synthetic_example_attempt_01"
    (workspace / "support" / "nested").mkdir(parents=True)
    (workspace / "candidate.sv").write_text("module TopModule; endmodule\n", encoding="utf-8")
    (workspace / "testbench.sv").write_text("module tb; endmodule\n", encoding="utf-8")
    (workspace / "support" / "helper.svh").write_text("// support\n", encoding="utf-8")

    for attempt_number in ("02", "03", "04"):
        (run / "verification" / f"attempt_{attempt_number}" / "workspace").mkdir(parents=True)

    recovery_workspace = run / "verification" / "attempt_01_retry_01" / "workspace" / "rtlgen_synthetic_example_attempt_01"
    recovery_workspace.mkdir(parents=True)
    (recovery_workspace / "candidate.sv").write_text("module TopModule; endmodule\n", encoding="utf-8")
    (recovery_workspace / "testbench.sv").write_text("module tb; endmodule\n", encoding="utf-8")

    report, code = validate_manual_rtl_run(run)
    assert code == 0, report
    assert report["ok"] is True
    assert report["errors"] == []
    assert not any(path.name == "reference.sv" for path in (run / "verification").rglob("*"))


def test_run_validation_accepts_packetized_verification_handoffs(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "pilot_001"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", run.parent)

    packet = run / "verification" / "attempt_02" / "packet_0001"
    (packet / "workspace" / "candidate").mkdir(parents=True)
    (packet / "candidate_manifest.jsonl").write_text("{}\n", encoding="utf-8")
    (packet / "verification_plan.jsonl").write_text("{}\n", encoding="utf-8")
    (packet / "run_instructions.md").write_text("manual handoff\n", encoding="utf-8")
    (packet / "workspace" / "candidate" / "candidate.sv").write_text(
        "module TopModule; endmodule\n", encoding="utf-8"
    )

    report, code = validate_manual_rtl_run(run)
    assert code == 0, report
    assert report["ok"] is True
    assert report["errors"] == []


@pytest.mark.parametrize(
    "relative",
    [
        "verification/attempt_00",
        "verification/attempt_05",
        "verification/attempt_1",
        "verification/attempt_01_extra",
        "verification/attempt_01_retry_0",
        "verification/attempt_01_retry_00",
        "verification/attempt_01_retry_bad",
        "verification/random",
        "verification/attempt_05/workspace",
        "verification/attempt_01/unexpected_directory",
    ],
)
def test_run_validation_rejects_invalid_verification_directories(tmp_path: Path, relative: str) -> None:
    run = tmp_path / "runs" / "pilot_001"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", run.parent)
    (run / relative).mkdir(parents=True)

    report, code = validate_manual_rtl_run(run)
    assert code == 1
    assert report["ok"] is False
    assert any("verification" in error for error in report["errors"])


@pytest.mark.parametrize(
    ("relative", "expected_code"),
    [
        ("repairs/attempt_02/nested", 0),
        ("repairs/attempt_01/nested", 1),
        ("repairs/attempt_05/nested", 1),
        ("repairs/attempt_2", 1),
        ("repairs/random", 1),
    ],
)
def test_run_validation_enforces_repair_attempt_directories(
    tmp_path: Path, relative: str, expected_code: int
) -> None:
    run = tmp_path / "runs" / "pilot_001"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", run.parent)
    (run / relative).mkdir(parents=True)

    report, code = validate_manual_rtl_run(run)
    assert code == expected_code
    assert report["ok"] is (expected_code == 0)
    if expected_code:
        assert any("repair" in error or "unexpected directory" in error for error in report["errors"])
    else:
        assert report["errors"] == []


def test_run_validation_rejects_leakage_and_wrong_records(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "pilot_001"
    initialize_manual_rtl_run("pilot_001", "VerilogEval", run.parent)
    (run / "normalization" / "packets" / "bad.json").write_text('{"path":"data/.local_data/secret"}\n', encoding="utf-8")
    (run / "verification" / "attempt_05").mkdir()
    (run / "normalization" / "packets" / "candidate_records.jsonl").write_text("{}\n", encoding="utf-8")
    report, code = validate_manual_rtl_run(run)
    assert code == 1
    assert any("private/local path" in error or "invalid verification attempt" in error for error in report["errors"])
    assert any("wrong folder" in error for error in report["errors"])
