from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dataset.rtl_generation_qualified20_normalization import (
    Qualified20NormalizationError,
    freeze_retry_qualification_lists,
    sha256_file,
)
from scripts.dataset.record_rtl_generation_qualification_execution import (
    QualificationExecutionReportError,
    record_execution_reports,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_freeze_retry_lists_is_append_only(tmp_path: Path) -> None:
    run_root = tmp_path / "pilot_007_assetfix_v004_retry_01"
    report_path = run_root / "reports" / "asset_qualification_validation.json"
    _write_json(
        report_path,
        {
            "schema_version": "rtl_asset_qualification_report_v0.1",
            "selected_tasks": 2,
            "rows": [
                {"source_id": "Prob070", "qualification_passed": True},
                {"source_id": "Prob074", "qualification_passed": True},
            ],
        },
    )
    root_list = run_root / "qualified_task_ids.txt"
    root_list.write_text("Prob070\nProb074\n", encoding="utf-8")

    result = freeze_retry_qualification_lists(
        retry_run_root=run_root,
        validation_report_path=report_path,
    )

    qualified = run_root / "reports" / "qualified_task_ids.txt"
    failed = run_root / "reports" / "failed_or_inconclusive_task_ids.txt"
    assert qualified.read_text(encoding="utf-8") == "Prob070\nProb074\n"
    assert failed.read_bytes() == b""
    assert result["qualified_source_ids"] == ["Prob070", "Prob074"]
    assert result["failed_or_inconclusive_source_ids"] == []
    assert result["qualified_task_ids_sha256"] == sha256_file(qualified)

    resumed = freeze_retry_qualification_lists(
        retry_run_root=run_root,
        validation_report_path=report_path,
    )
    assert resumed["qualified_task_ids_sha256"] == result["qualified_task_ids_sha256"]


def test_freeze_retry_lists_rejects_disagreeing_validator_list(tmp_path: Path) -> None:
    run_root = tmp_path / "pilot_007_assetfix_v004_retry_01"
    report_path = run_root / "reports" / "asset_qualification_validation.json"
    _write_json(
        report_path,
        {
            "schema_version": "rtl_asset_qualification_report_v0.1",
            "selected_tasks": 2,
            "rows": [
                {"source_id": "Prob070", "qualification_passed": True},
                {"source_id": "Prob074", "qualification_passed": True},
            ],
        },
    )
    (run_root / "qualified_task_ids.txt").write_text("Prob074\nProb070\n", encoding="utf-8")

    with pytest.raises(Qualified20NormalizationError):
        freeze_retry_qualification_lists(
            retry_run_root=run_root,
            validation_report_path=report_path,
        )


@pytest.mark.parametrize("nested_qualification_layout", [False, True])
def test_execution_reports_are_sanitized_and_append_only(
    tmp_path: Path, nested_qualification_layout: bool
) -> None:
    run_root = tmp_path / "pilot_007_assetfix_v004_retry_01"
    qualification_root = (
        run_root / "qualification" / "attempt_01"
        if nested_qualification_layout
        else run_root
    )
    control_root = tmp_path / "control"
    (run_root / "reports").mkdir(parents=True)
    (qualification_root / "input").mkdir(parents=True)
    (qualification_root / "staged").mkdir()
    control_root.mkdir()
    (qualification_root / "input/candidate_manifest.jsonl").write_text("{}\n", encoding="utf-8")
    evidence_rows = []
    for index in range(6):
        positive = index < 2
        evidence_rows.append({
            "candidate_id": f"case_{index}__public_spec_candidate" if positive else f"case_{index}__mutation",
            "task_id": f"task_{index}",
            "source_id": f"source_{index}",
            "accepted": positive,
            "failure_category": "passed" if positive else "functional_mismatch",
            "checks": {
                "compile": {"candidate": {"attempted": True, "passed": True}},
                "simulation": {"candidate_passes": {"attempted": True, "passed": positive}},
            },
            "mismatch_summary": {"maximum_count": 0 if positive else 1, "timeout_reported": False},
        })
    evidence_path = qualification_root / "staged/candidate_evidence.jsonl"
    evidence_path.write_text("".join(json.dumps(row) + "\n" for row in evidence_rows), encoding="utf-8")
    sidecar_path = qualification_root / "staged/candidate_evidence.jsonl.runner.json"
    _write_json(sidecar_path, {
        "image_id": "image",
        "rtlbench_commit": "commit",
        "partial_evidence_sha256": None,
    })
    _write_json(run_root / "reports/asset_qualification_preflight.json", {"workspace_tree_sha256": "workspace"})
    _write_json(run_root / "reports/asset_qualification_authorization.json", {
        "exact_command": ["runner"],
        "runner": {"image_id": "image", "rtlbench_commit": "commit"},
    })
    _write_json(run_root / "reports/asset_qualification_validation.json", {"qualification_passed": True})
    for name in ("manifest-before.sha256", "manifest-after.sha256", "volumes-before.txt", "volumes-after.txt", "containers-before.txt", "containers-after.txt"):
        (control_root / name).write_bytes(b"")
    (control_root / "isolated-runner-exit.txt").write_text("isolated_runner_exit=0\n", encoding="utf-8")

    result = record_execution_reports(
        run_root=run_root,
        control_root=control_root,
        authorization_path=run_root / "reports/asset_qualification_authorization.json",
        validation_path=run_root / "reports/asset_qualification_validation.json",
    )
    assert result["case_count"] == 6
    assert json.loads((run_root / "reports/asset_qualification_execution.json").read_text())["invocation_count"] == 1
    assert json.loads((run_root / "reports/asset_qualification_cleanup.json").read_text())["runtime_resources_leaked"] is False
    with pytest.raises(QualificationExecutionReportError):
        record_execution_reports(
            run_root=run_root,
            control_root=control_root,
            authorization_path=run_root / "reports/asset_qualification_authorization.json",
            validation_path=run_root / "reports/asset_qualification_validation.json",
        )
