from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.dataset import rtl_generation_asset_qualification as qualification


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source_ids = tuple(f"Prob{i:03d}" for i in range(20))
    monkeypatch.setattr(qualification, "BATCH20_SOURCE_IDS", source_ids)
    monkeypatch.setattr(qualification, "SOURCE_COMMIT", "a" * 40)
    monkeypatch.setattr(qualification, "SOURCE_TREE_SHA256", "b" * 64)
    monkeypatch.setattr(qualification, "BASE_INVENTORY_SHA256", "c" * 64)
    monkeypatch.setattr(qualification, "BASE_SPLIT_SHA256", "d" * 64)
    monkeypatch.setattr(qualification, "CORRECTION_VERSION", "assetfix_test")

    inventory_rows = []
    for source_id in source_ids:
        inventory_rows.append({
            "source_id": source_id,
            "task_id": f"task_{source_id}",
            "top_module": "TopModule",
        })
    inventory = tmp_path / "inventory.jsonl"
    _write_jsonl(inventory, inventory_rows)
    monkeypatch.setattr(qualification, "BASE_INVENTORY_SHA256", _sha(inventory))

    split = tmp_path / "split.json"
    _write_json(split, {"splits": {"train": list(source_ids), "validation": [], "test": []}})
    monkeypatch.setattr(qualification, "BASE_SPLIT_SHA256", _sha(split))

    ids = tmp_path / "ids.txt"
    ids.write_text("\n".join(source_ids) + "\n", encoding="utf-8")
    selection = tmp_path / "selection.json"
    _write_json(selection, {
        "schema_version": qualification.SELECTION_SCHEMA_VERSION,
        "source_commit": qualification.SOURCE_COMMIT,
        "source_tree_sha256": qualification.SOURCE_TREE_SHA256,
        "base_inventory_sha256": qualification.BASE_INVENTORY_SHA256,
        "base_split_sha256": qualification.BASE_SPLIT_SHA256,
        "correction_version": qualification.CORRECTION_VERSION,
        "selection_ids_sha256": _sha(ids),
    })

    correction_root = tmp_path / "correction"
    correction_rows = []
    author_root = tmp_path / "authors"
    author_rows = []
    for source_id in source_ids:
        tb = correction_root / "tasks" / source_id / "testbench.sv"
        tb.parent.mkdir(parents=True, exist_ok=True)
        tb.write_text("module tb; TopModule dut(); initial begin $display(\"Mismatches: %0d\", 0); $finish; end endmodule\n", encoding="utf-8")
        correction_rows.append({
            "source_id": source_id,
            "task_id": f"task_{source_id}",
            "top_module": "TopModule",
            "split": "train",
            "correction_version": qualification.CORRECTION_VERSION,
            "upstream_commit": qualification.SOURCE_COMMIT,
            "source_tree_sha256": qualification.SOURCE_TREE_SHA256,
            "frozen_split_sha256": qualification.BASE_SPLIT_SHA256,
            "qualification_status": "pending_isolated_qualification",
            "verification_readiness": "pending_qualification",
            "dependency_closure": "passed",
            "support_files": [],
            "reference_modified": False,
            "reference_copied_to_support": False,
            "testbench_path": f"tasks/{source_id}/testbench.sv",
            "corrected_testbench_sha256": _sha(tb),
            "mutation_contracts": [
                {"kind": "positive", "name": "public_spec_candidate"},
                {"kind": "negative", "name": "negative_one"},
                {"kind": "negative", "name": "negative_two"},
            ],
        })
        source_dir = author_root / source_id
        source_dir.mkdir(parents=True)
        for name in ("positive", "negative_one", "negative_two"):
            (source_dir / f"{name}.sv").write_text("module TopModule; endmodule\n", encoding="utf-8")
        author_rows.append({
            "schema_version": qualification.AUTHORING_ROW_SCHEMA_VERSION,
            "source_id": source_id,
            "task_id": f"task_{source_id}",
            "top_module": "TopModule",
            "positive_rtl_path": f"{source_id}/positive.sv",
            "negative_mutations": [
                {"name": "negative_one", "rtl_path": f"{source_id}/negative_one.sv", "authoring_method": "manual", "oracle_basis": "public_specification_only", "expected_outcome": "rejected"},
                {"name": "negative_two", "rtl_path": f"{source_id}/negative_two.sv", "authoring_method": "manual", "oracle_basis": "public_specification_only", "expected_outcome": "rejected"},
            ],
            "reference_used": False,
            "support_files": [],
        })
    for path in author_root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
        else:
            path.chmod(0o600)
    author_root.chmod(0o700)
    correction_manifest = tmp_path / "correction.jsonl"
    _write_jsonl(correction_manifest, correction_rows)
    author_manifest = tmp_path / "authors.jsonl"
    _write_jsonl(author_manifest, author_rows)
    return source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root


def test_preparation_creates_exactly_sixty_cases_and_runner_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    report = qualification.prepare_qualification_input(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    assert report["case_count"] == 60
    assert report["positive_case_count"] == 20
    assert report["negative_case_count"] == 40
    cases = qualification._load_jsonl(output / "case_manifest.jsonl")
    manifest = qualification._load_jsonl(output / "input/candidate_manifest.jsonl")
    assert [row["source_id"] for row in cases[::3]] == list(source_ids)
    assert len(manifest) == 60
    assert len({(row["task_id"], row["attempt"]) for row in manifest}) == 60
    assert not (output / "input/run_instructions.md").exists()
    assert (output / "staged").is_dir()
    assert (output / "staged").stat().st_mode & 0o777 == 0o700
    assert not list((output / "staged").iterdir())
    assert not list(output.rglob("reference.sv"))


def test_preparation_accepts_bounded_lineage_metadata_on_authoring_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    rows = qualification._load_jsonl(author_manifest)
    rows[0]["lineage"] = "qualification_retry_overlay"
    rows[0]["prior_assetfix_versions"] = ["assetfix_v005"]
    rows[0]["selection_role"] = "qualification_retry_after_prior_failure"
    _write_jsonl(author_manifest, rows)

    output = tmp_path / "qualification"
    report = qualification.prepare_qualification_input(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    assert report["case_count"] == 60


def _synthetic_evidence(case_rows, qualification_root: Path):
    rows = []
    for case in case_rows:
        positive = case["kind"] == "positive"
        rows.append({
            "schema_version": "rtl_candidate_evidence_v0.1",
            "candidate_id": case["candidate_id"],
            "task_id": case["task_id"],
            "source_id": case["source_id"],
            "attempt": case["attempt"],
            "top_module": "TopModule",
            "accepted": positive,
            "failure_category": "passed" if positive else "functional_mismatch",
            "input_hashes": {"candidate_rtl_sha256": case["candidate_sha256"], "testbench_sha256": case["testbench_sha256"], "support_files": []},
            "checks": {
                "compile": {"candidate": {"attempted": True, "passed": True, "reason": None}},
                "simulation": {"candidate_passes": {"attempted": True, "passed": positive, "reason": None if positive else "functional_mismatch"}},
            },
            "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [0 if positive else 2], "maximum_count": 0 if positive else 2, "timeout_reported": False},
            "diagnostics": [],
        })
    return rows


def test_aggregation_qualifies_only_positive_and_detected_negative_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    qualification.prepare_qualification_input(
        selection_path=selection, ids_path=ids, correction_manifest_path=correction_manifest,
        correction_root=correction_root, inventory_path=inventory, split_path=split,
        authoring_manifest_path=author_manifest, authoring_root=author_root, output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    cases = qualification._load_jsonl(output / "case_manifest.jsonl")
    evidence_path = tmp_path / "candidate_evidence.jsonl"
    _write_jsonl(evidence_path, _synthetic_evidence(cases, output))
    sidecar_path = tmp_path / "candidate_evidence.jsonl.runner.json"
    _write_json(sidecar_path, {
        "schema_version": qualification.RUNNER_SCHEMA_VERSION,
        "profile": "pilot-docker", "runtime": "docker", "runtime_mode": "rootful-daemon", "rootless": False,
        "image_id": qualification.IMAGE_ID, "rtlbench_commit": qualification.RTLBench_COMMIT,
        "network_policy": "none", "partial_evidence_sha256": None,
        "manifest_sha256": _sha(output / "input/candidate_manifest.jsonl"),
        "workspace_tree_sha256": qualification._workspace_tree_sha256(output / "input/workspace"),
        "evidence_sha256": _sha(evidence_path),
    })
    report = qualification.aggregate_qualification_evidence(
        qualification_root=output, evidence_path=evidence_path, sidecar_path=sidecar_path,
        report_output=tmp_path / "report.json", evidence_output=tmp_path / "qualification_evidence.jsonl",
    )
    assert report["qualification_passed"] is True
    assert report["qualified_tasks"] == 20
    assert report["negative_mutations_detected"] == 40
    assert (output / "qualified_task_ids.txt").read_text(encoding="utf-8").splitlines() == list(source_ids)


def test_aggregation_rejects_runner_hash_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    _, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    qualification.prepare_qualification_input(
        selection_path=selection, ids_path=ids, correction_manifest_path=correction_manifest,
        correction_root=correction_root, inventory_path=inventory, split_path=split,
        authoring_manifest_path=author_manifest, authoring_root=author_root, output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    cases = qualification._load_jsonl(output / "case_manifest.jsonl")
    evidence_path = tmp_path / "candidate_evidence.jsonl"
    _write_jsonl(evidence_path, _synthetic_evidence(cases, output))
    sidecar_path = tmp_path / "sidecar.json"
    _write_json(sidecar_path, {"schema_version": qualification.RUNNER_SCHEMA_VERSION, "profile": "pilot-docker", "runtime": "docker", "runtime_mode": "rootful-daemon", "rootless": False, "image_id": qualification.IMAGE_ID, "rtlbench_commit": qualification.RTLBench_COMMIT, "network_policy": "none", "partial_evidence_sha256": None, "manifest_sha256": "0" * 64, "workspace_tree_sha256": qualification._workspace_tree_sha256(output / "input/workspace"), "evidence_sha256": _sha(evidence_path)})
    report = qualification.aggregate_qualification_evidence(
        qualification_root=output, evidence_path=evidence_path, sidecar_path=sidecar_path,
        report_output=tmp_path / "report.json", evidence_output=tmp_path / "qualification_evidence.jsonl",
    )
    assert report["qualification_passed"] is False
    assert any("manifest_sha256" in error for error in report["errors"])
    assert not (output / "qualified_task_ids.txt").exists()


def _preflight_authorization(
    output: Path,
    preparation: dict,
    tmp_path: Path,
    *,
    create_staged: bool = True,
) -> dict:
    if create_staged:
        staged = output / "staged"
        staged.mkdir(mode=0o700, exist_ok=True)
        staged.chmod(0o700)
    input_root = output / "input"
    return {
        "schema_version": qualification.AUTHORIZATION_SCHEMA_VERSION,
        "run_id": qualification.RUN_ID,
        "status": "authorized_once",
        "authorization_scope": "qualification_only",
        "source_commit": qualification.SOURCE_COMMIT,
        "source_tree_sha256": qualification.SOURCE_TREE_SHA256,
        "frozen_split_sha256": qualification.BASE_SPLIT_SHA256,
        "selection_ids_sha256": preparation["selection_ids_sha256"],
        "correction_manifest_sha256": preparation["correction_manifest_sha256"],
        "correction_version": qualification.CORRECTION_VERSION,
        "selected_source_ids": list(qualification.BATCH20_SOURCE_IDS),
        "case_count": 60,
        "positive_case_count": 20,
        "negative_case_count": 40,
        "qualification_status_before": "pending_isolated_qualification",
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "candidate_manifest_sha256": preparation["candidate_manifest_sha256"],
        "workspace_tree_sha256": preparation["workspace_tree_sha256"],
        "runner": {
            "profile": "pilot-docker",
            "runtime": "docker",
            "runtime_mode": "rootful-daemon",
            "rootless": False,
            "image_id": qualification.IMAGE_ID,
            "rtlbench_commit": qualification.RTLBench_COMMIT,
            "network_policy": "none",
            "runtime_user": "65532:65532",
        },
        "qualification_input": str(input_root.resolve()),
        "exact_command": [
            "/home/armmy-server/apps/RTLBench/.venv/bin/python",
            "runner/run_isolated.py",
            "--profile",
            "pilot-docker",
            "--acknowledge-rootful-runtime",
            "--image",
            qualification.IMAGE_ID,
            "--input",
            str(input_root.resolve()),
            "--output",
            str((input_root.parent / "staged/candidate_evidence.jsonl").resolve()),
        ],
        "authorized_invocations": 1,
    }


def _prepared_preflight_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_staged: bool = True,
):
    values = _fixture(tmp_path, monkeypatch)
    source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    preparation = qualification.prepare_qualification_input(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    if not create_staged:
        (output / "staged").rmdir()
    reports = output / "reports"
    reports.mkdir(mode=0o700)
    reports.chmod(0o700)
    authorization_path = reports / "authorization.json"
    _write_json(
        authorization_path,
        _preflight_authorization(output, preparation, tmp_path, create_staged=create_staged),
    )
    authorization_path.chmod(0o600)
    return values, output, authorization_path


def _validate_prepared_fixture(values, output: Path, authorization_path: Path, tmp_path: Path):
    _, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    return qualification.validate_prepared_qualification(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        qualification_root=output,
        authorization_path=authorization_path,
        report_output=tmp_path / "preflight.json",
    )


def test_preflight_validates_the_staged_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    preparation = qualification.prepare_qualification_input(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    reports = output / "reports"
    reports.mkdir(mode=0o700)
    reports.chmod(0o700)
    authorization_path = reports / "authorization.json"
    _write_json(authorization_path, _preflight_authorization(output, preparation, tmp_path))
    authorization_path.chmod(0o600)
    report = qualification.validate_prepared_qualification(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        qualification_root=output,
        authorization_path=authorization_path,
        report_output=tmp_path / "preflight.json",
    )
    assert report["status"] == "ready_for_isolated_execution"
    assert report["selected_task_count"] == 20
    assert report["candidate_case_count"] == 60
    assert report["staged_runner_entries"] == ["candidate_manifest.jsonl", "workspace"]
    assert report["instructions_staged"] is False
    assert report["verification_plan_staged"] is False
    assert report["reference_rtl_supplied"] is False
    assert report["selected_source_ids"] == list(source_ids)


def test_preflight_rejects_missing_staged_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values, output, authorization_path = _prepared_preflight_fixture(
        tmp_path, monkeypatch, create_staged=False
    )
    with pytest.raises(qualification.QualificationError):
        _validate_prepared_fixture(values, output, authorization_path, tmp_path)


def test_preflight_rejects_staged_path_that_is_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values, output, authorization_path = _prepared_preflight_fixture(
        tmp_path, monkeypatch, create_staged=False
    )
    (output / "staged").write_text("not a directory\n", encoding="utf-8")
    with pytest.raises(qualification.QualificationError):
        _validate_prepared_fixture(values, output, authorization_path, tmp_path)


def test_preflight_rejects_staged_directory_with_wrong_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values, output, authorization_path = _prepared_preflight_fixture(tmp_path, monkeypatch)
    (output / "staged").chmod(0o755)
    with pytest.raises(qualification.QualificationError):
        _validate_prepared_fixture(values, output, authorization_path, tmp_path)


def test_preflight_rejects_nonempty_staged_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values, output, authorization_path = _prepared_preflight_fixture(tmp_path, monkeypatch)
    marker = output / "staged" / "unexpected-output"
    marker.write_text("unexpected\n", encoding="utf-8")
    marker.chmod(0o600)
    with pytest.raises(qualification.QualificationError):
        _validate_prepared_fixture(values, output, authorization_path, tmp_path)


def test_preflight_accepts_empty_0700_staged_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values, output, authorization_path = _prepared_preflight_fixture(tmp_path, monkeypatch)
    report = _validate_prepared_fixture(values, output, authorization_path, tmp_path)
    assert report["status"] == "ready_for_isolated_execution"


def test_preflight_accepts_authorization_in_run_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    source_ids, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    preparation = qualification.prepare_qualification_input(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    reports = output / "reports"
    reports.mkdir(mode=0o700)
    authorization_path = reports / "asset_qualification_authorization.json"
    _write_json(authorization_path, _preflight_authorization(output, preparation, tmp_path))
    authorization_path.chmod(0o600)
    report = qualification.validate_prepared_qualification(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        qualification_root=output,
        authorization_path=authorization_path,
        report_output=tmp_path / "preflight.json",
    )
    assert report["status"] == "ready_for_isolated_execution"
    assert report["selected_source_ids"] == list(source_ids)


def test_preflight_rejects_wrong_case_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = _fixture(tmp_path, monkeypatch)
    _, inventory, split, ids, selection, correction_manifest, correction_root, author_manifest, author_root = values
    output = tmp_path / "qualification"
    preparation = qualification.prepare_qualification_input(
        selection_path=selection,
        ids_path=ids,
        correction_manifest_path=correction_manifest,
        correction_root=correction_root,
        inventory_path=inventory,
        split_path=split,
        authoring_manifest_path=author_manifest,
        authoring_root=author_root,
        output_root=output,
        expected_correction_manifest_sha256=_sha(correction_manifest),
    )
    cases = qualification._load_jsonl(output / "case_manifest.jsonl")
    cases[0], cases[3] = cases[3], cases[0]
    _write_jsonl(output / "case_manifest.jsonl", cases)
    reports = output / "reports"
    reports.mkdir(mode=0o700)
    reports.chmod(0o700)
    authorization_path = reports / "authorization.json"
    _write_json(authorization_path, _preflight_authorization(output, preparation, tmp_path))
    authorization_path.chmod(0o600)
    with pytest.raises(qualification.QualificationError, match="case manifest does not preserve"):
        qualification.validate_prepared_qualification(
            selection_path=selection,
            ids_path=ids,
            correction_manifest_path=correction_manifest,
            correction_root=correction_root,
            inventory_path=inventory,
            split_path=split,
            authoring_manifest_path=author_manifest,
            authoring_root=author_root,
            qualification_root=output,
            authorization_path=authorization_path,
            report_output=tmp_path / "preflight.json",
        )
