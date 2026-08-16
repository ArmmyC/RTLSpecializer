from __future__ import annotations

import json

from scripts.dataset.rtl_generation_smoke_run import (
    EXPECTED_SOURCE_IDS,
    QUALIFICATION_REPORT_SCHEMA_VERSION,
    _qualification_diagnostic_errors,
    _workspace_tree_sha256,
    validate_qualification_report_metadata,
)


def test_workspace_tree_hash_is_path_independent_and_rejects_symlinks(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "nested").mkdir(parents=True)
    (workspace / "nested" / "candidate.sv").write_text("module TopModule; endmodule\n", encoding="utf-8")
    first = _workspace_tree_sha256(workspace)

    moved = tmp_path / "moved" / "workspace"
    moved.parent.mkdir()
    workspace.rename(moved)
    assert _workspace_tree_sha256(moved) == first

    link = moved / "link.sv"
    link.symlink_to(moved / "nested" / "candidate.sv")
    try:
        _workspace_tree_sha256(moved)
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("workspace symlink was accepted")


def test_qualification_report_metadata_requires_passed_rows(tmp_path) -> None:
    report = {
        "schema_version": QUALIFICATION_REPORT_SCHEMA_VERSION,
        "correction_version": "assetfix_v002",
        "qualification_passed": True,
        "rows": [
            {"source_id": source_id, "qualification_passed": True}
            for source_id in EXPECTED_SOURCE_IDS
        ],
    }
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report) + "\n", encoding="utf-8")
    loaded, errors = validate_qualification_report_metadata(path)
    assert loaded == report
    assert errors == []

    report["rows"][0]["qualification_passed"] = False
    path.write_text(json.dumps(report) + "\n", encoding="utf-8")
    _, errors = validate_qualification_report_metadata(path)
    assert any("row 1" in error for error in errors)


def test_qualification_accepts_sanitized_result_diagnostics() -> None:
    assert _qualification_diagnostic_errors([]) == []
    assert _qualification_diagnostic_errors([
        "simulation: returncode=0 Mismatches: 4 <workspace><path> $finish called at 16 (1s)",
        "compile: returncode=0 <workspace><path> warning: @* found no sensitivities so it will never trigger.",
    ]) == []


def test_qualification_rejects_unrecognized_or_private_diagnostics() -> None:
    errors = _qualification_diagnostic_errors([
        "simulation: returncode=1 Mismatches: 0 <workspace><path> $finish called at 1 (1s)",
        "simulation: /home/private/reference.sv",
    ])
    assert len(errors) == 2
