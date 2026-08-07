from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dataset.export_rtl_teacher_generation_packets import main as export_main
from scripts.dataset.rtl_generation_teacher_preparation import (
    BINDING_SCHEMA_VERSION,
    EXPECTED_QUALIFIED_BINDING_SCHEMA,
    EXPECTED_QUALIFIED_LIST_SHA256,
    EXPECTED_QUALIFIED_SOURCE_LIST_SHA256,
    FROZEN_SPLIT_SHA256,
    SOURCE_COMMIT,
    SOURCE_TREE_SHA256,
    _text_list_sha256,
    _validate_qualified_binding,
    packet_set_sha256,
    validate_teacher_packet_set,
)
from scripts.dataset.rtl_manual_teacher_verification import (
    _plan_row,
    _validate_plan,
)
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT


def _qualified_binding_fixture() -> dict:
    rows = [
        {
            "source_id": f"Source{index:03d}",
            "task_id": f"Task{index:03d}",
            "qualification_result": "passed",
            "qualification_status": "qualified",
            "reference_supplied": False,
            "support_files": [],
        }
        for index in range(16)
    ]
    return {
        "schema_version": EXPECTED_QUALIFIED_BINDING_SCHEMA,
        "correction_version": "assetfix_v003",
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "frozen_split_sha256": FROZEN_SPLIT_SHA256,
        "selection_ids_sha256": "e1927846320a465a49e039e7a8f3518a12424d14e08972a10a1fd232a1d16bbd",
        "correction_manifest_sha256": "2b7cbe7a82f0b73e9960216254565600a9df46bebd66c71fa45f0f401501b8c1",
        "qualification_report_sha256": "8b3ac80a6cea697b29fc5523529791811b62a6f035850083adbbbcf4ba3c7729",
        "qualification_evidence_sha256": "b7f3ac3a9127bd9eeaf9c033da5eb08fa36142e13d941d9270c5e47a9810c104",
        "raw_runner_evidence_sha256": "954dbb606f721c568df58f9032ce63b72cd6e3e5edb302210cba67fa9d1e4eae",
        "runner_sidecar_sha256": "35c81fe662908ffb383991b0e505a3486330a9fe1cfaac53c6b07ec272f93e4f",
        "qualified_task_ids_sha256": EXPECTED_QUALIFIED_LIST_SHA256,
        "failed_task_ids_sha256": "d53b327a37a78ec724e70f0cdc48f4fba069ba9f500726da10cb79612240cb72",
        "qualified_task_count": 16,
        "failed_task_count": 4,
        "normalization_allowed": True,
        "teacher_generation_allowed": False,
        "reference_rtl_supplied": False,
        "support_file_count": 0,
        "rows": rows,
    }


def test_qualified_binding_rejects_wrong_hash_and_order(tmp_path: Path) -> None:
    binding = _qualified_binding_fixture()
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(binding), encoding="utf-8")

    loaded, rows = _validate_qualified_binding(path)
    assert loaded["schema_version"] == EXPECTED_QUALIFIED_BINDING_SCHEMA
    assert len(rows) == 16

    binding["source_commit"] = "0" * 40
    path.write_text(json.dumps(binding), encoding="utf-8")
    with pytest.raises(ValueError, match="source_commit"):
        _validate_qualified_binding(path)

    binding = _qualified_binding_fixture()
    binding["rows"][0], binding["rows"][1] = binding["rows"][1], binding["rows"][0]
    path.write_text(json.dumps(binding), encoding="utf-8")
    _, rows = _validate_qualified_binding(path)
    assert [row["task_id"] for row in rows[:2]] == ["Task001", "Task000"]


def test_qualified_binding_accepts_v003_reference_privacy_field(tmp_path: Path) -> None:
    binding = _qualified_binding_fixture()
    binding["schema_version"] = "rtl_generation_qualified_subset_binding_v0.3"
    binding["qualified_correction_manifest_sha256"] = binding.pop("correction_manifest_sha256")
    binding["derived_qualification_evidence_sha256"] = binding.pop("raw_runner_evidence_sha256")
    binding["qualification_runner_sidecar_sha256"] = binding.pop("runner_sidecar_sha256")
    for row in binding["rows"]:
        row["reference_copied_to_support"] = row.pop("reference_supplied")
    path = tmp_path / "binding-v003.json"
    path.write_text(json.dumps(binding), encoding="utf-8")

    loaded, rows = _validate_qualified_binding(path)
    assert loaded["schema_version"] == "rtl_generation_qualified_subset_binding_v0.3"
    assert len(rows) == 16


def _make_sixteen_task_fixture(tmp_path: Path) -> Path:
    original = json.loads((FIXTURE_ROOT / "generation_tasks.jsonl").read_text(encoding="utf-8"))
    rows = []
    for index in range(16):
        row = dict(original)
        row["task_id"] = f"Task{index:03d}"
        row["source_id"] = f"Source{index:03d}"
        rows.append(row)
    path = tmp_path / "generation_tasks.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    return path


def _make_packet_run(tmp_path: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    tasks = _make_sixteen_task_fixture(tmp_path)
    packet_dir = run / "teacher" / "packets"
    packet_dir.mkdir(parents=True)
    (run / "teacher" / "responses").mkdir(parents=True)
    (run / "verification").mkdir()
    (run / "repairs").mkdir()
    (run / "tasks").mkdir()
    (run / "reports").mkdir()
    (run / "tasks" / "generation_tasks.jsonl").write_bytes(tasks.read_bytes())
    binding = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "packet_export_allowed": True,
        "teacher_response_allowed": False,
        "qualified_order": [
            {"source_id": f"Source{index:03d}", "task_id": f"Task{index:03d}", "top_module": "TopModule"}
            for index in range(16)
        ],
    }
    binding_path = run / "reports" / "teacher_generation_binding.json"
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    assert export_main([
        "--tasks", str(tasks),
        "--output-dir", str(packet_dir),
        "--batch-size", "1",
        "--limit", "16",
        "--json",
    ]) == 0
    return run, binding_path


def test_packet_set_hash_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        (directory / "packet_0001.json").write_bytes(b"{}\n")
        (directory / "packet_0001.md").write_bytes(b"public\n")
    first_hash, first_files = packet_set_sha256(first)
    second_hash, second_files = packet_set_sha256(second)
    assert first_hash == second_hash
    assert first_files == second_files


def test_packet_validation_rejects_order_and_extra_entries(tmp_path: Path) -> None:
    run, binding_path = _make_packet_run(tmp_path)
    report, code = validate_teacher_packet_set(
        run,
        binding_path=binding_path,
        output_path=run / "reports" / "validation.json",
    )
    assert code == 0, report
    assert report["ok"] is True
    assert report["row_count"] == 16
    assert report["teacher_response_allowed"] is True

    validation_path = run / "reports" / "validation.json"
    validation_path.unlink()
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["qualified_order"][0], binding["qualified_order"][1] = (
        binding["qualified_order"][1],
        binding["qualified_order"][0],
    )
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    report, code = validate_teacher_packet_set(
        run,
        binding_path=binding_path,
        output_path=validation_path,
    )
    assert code != 0
    assert "mismatch" in report["errors"][0]

    binding["qualified_order"][0], binding["qualified_order"][1] = (
        binding["qualified_order"][1],
        binding["qualified_order"][0],
    )
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    (run / "teacher" / "packets" / "unexpected.txt").write_text("x", encoding="utf-8")
    report, code = validate_teacher_packet_set(
        run,
        binding_path=binding_path,
        output_path=validation_path,
    )
    assert code != 0
    assert "unexpected" in report["errors"][0]


def test_packet_validation_allows_published_teacher_returns(tmp_path: Path) -> None:
    run, binding_path = _make_packet_run(tmp_path)
    response_dir = run / "teacher" / "responses"
    for index in range(1, 17):
        (response_dir / f"packet_{index:04d}_response.json").write_text(
            '{"rows": []}\n', encoding="utf-8"
        )
    (run / "teacher" / "candidate_records.jsonl").write_text(
        '{"schema_version":"rtl_teacher_candidate_record_v0.1"}\n',
        encoding="utf-8",
    )

    report, code = validate_teacher_packet_set(
        run,
        binding_path=binding_path,
        output_path=run / "reports" / "validation.json",
    )

    assert code == 0, report
    assert report["ok"] is True
    assert report["teacher_responses_present"] is True
    assert report["candidate_generation_started"] is True


def test_teacher_generation_binding_is_preserved_in_verification_plan() -> None:
    digest = "a" * 64
    binding = {
        "schema_version": "rtl_generation_teacher_handoff_binding_v0.1",
        "teacher_generation_binding_sha256": digest,
        "packet_validation_report_sha256": digest,
        "task_id": "task_001",
        "source_id": "Source001",
        "candidate_id": "task_001_attempt_01",
        "attempt": 1,
        "top_module": "TopModule",
        "normalization_packet_sha256": digest,
        "normalization_response_sha256": digest,
        "qualified_task_list_sha256": digest,
        "qualification_binding_sha256": digest,
        "qualification_evidence_sha256": digest,
        "qualification_runner_sidecar_sha256": digest,
        "corrected_testbench_sha256": digest,
        "task_record_sha256": digest,
        "asset_record_sha256": digest,
        "correction_version": "assetfix_v003",
        "source_commit": "b" * 40,
        "source_tree_sha256": digest,
        "frozen_split_sha256": digest,
        "qualification_passed": True,
        "reference_rtl_supplied": False,
        "support_files": [],
    }
    plan = _plan_row(
        {
            "candidate_id": "task_001_attempt_01",
            "task_id": "task_001",
            "source_id": "Source001",
            "attempt": 1,
        },
        {"task_id": "task_001", "source_id": "Source001", "top_module": "TopModule"},
        "task_001_attempt_01/candidate.sv",
        "task_001_attempt_01/testbench.sv",
        [],
        {"candidate_rtl_sha256": digest, "testbench_sha256": digest, "support_files": []},
        teacher_generation_binding=binding,
    )
    assert _validate_plan(plan, "synthetic plan")["teacher_generation_binding"] == binding

    v004_plan = dict(plan)
    v004_plan["teacher_generation_binding"] = {**binding, "correction_version": "assetfix_v004"}
    assert _validate_plan(v004_plan, "synthetic v004 plan")["teacher_generation_binding"]["correction_version"] == "assetfix_v004"

    v005_plan = dict(plan)
    v005_plan["teacher_generation_binding"] = {**binding, "correction_version": "assetfix_v005"}
    assert _validate_plan(v005_plan, "synthetic v005 plan")["teacher_generation_binding"]["correction_version"] == "assetfix_v005"

    invalid = dict(plan)
    invalid["teacher_generation_binding"] = {**binding, "correction_version": "assetfix_v002"}
    with pytest.raises(ValueError, match="correction_version"):
        _validate_plan(invalid, "synthetic plan")
