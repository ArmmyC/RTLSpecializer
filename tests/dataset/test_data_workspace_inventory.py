from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest

from scripts.dataset.data_workspace import collect_data_workspace_inventory
from scripts.dataset.data_workspace_layout import WorkspaceError, build_inventory, inventory_data_workspace


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _task(source_id: str) -> dict:
    return {
        "schema_version": "rtl_task.v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "prompt": f"Review {source_id}",
        "artifacts": {"rtl_code": "module dut; endmodule\n"},
    }


def _answer(source_id: str) -> dict:
    return {
        "schema_version": "rtl_answer.v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "issue_summary": [],
        "time_reasoning": {},
        "space_reasoning": {},
        "safe_optimization": {},
        "functional_risk": [],
        "verification_plan": [],
        "claim_levels": {
            "correctness": "suggestion_only",
            "area": "insufficient_evidence",
            "activity": "insufficient_evidence",
            "power": "insufficient_evidence",
        },
        "evidence_used": [],
        "limitations": [],
    }


def test_inventory_detects_row_counts_and_roles(tmp_path) -> None:
    data_dir = tmp_path / "data"
    task_path = data_dir / "review" / "rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl"
    teacher_return_path = data_dir / "review" / "rtlcoder_teacher_answer_returns_1000" / "batch_001_answers_rtl_answer_v0_1.json"
    repaired_path = data_dir / "review" / "repaired_rtl_answer_batches" / "rtlcoder_teacher_answer_returns_1000" / "batch_001_answers_rtl_answer_v0_1.json"
    assembled_path = data_dir / "review" / "rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl"
    report_path = data_dir / "review" / "rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json"

    _write_jsonl(task_path, [_task("rtlcoder_001"), _task("rtlcoder_002")])
    _write_json(teacher_return_path, {"answers": [_answer("rtlcoder_001"), _answer("rtlcoder_002")]})
    _write_json(repaired_path, {"answers": [_answer("rtlcoder_001"), _answer("rtlcoder_002")]})
    _write_jsonl(assembled_path, [_answer("rtlcoder_001"), _answer("rtlcoder_002")])
    _write_json(report_path, {"ok": True, "selected_answers": 2})

    result, code = collect_data_workspace_inventory(data_dir=data_dir)

    assert code == 0, result
    by_path = {entry["path"]: entry for entry in result["files"]}
    assert by_path["review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl"]["row_count"] == 2
    assert by_path["review/rtlcoder_teacher_answer_returns_1000/batch_001_answers_rtl_answer_v0_1.json"]["row_count"] == 2
    assert by_path["review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl"]["detected_role"] == "normalized_task"
    assert by_path["review/rtlcoder_teacher_answer_returns_1000/batch_001_answers_rtl_answer_v0_1.json"]["detected_role"] == "teacher_answer_batch"
    assert by_path["review/repaired_rtl_answer_batches/rtlcoder_teacher_answer_returns_1000/batch_001_answers_rtl_answer_v0_1.json"]["detected_role"] == "repaired_answer_batch"
    assert by_path["review/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl"]["detected_role"] == "assembled_answer_jsonl"
    assert by_path["review/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json"]["detected_role"] == "assembly_report"


def test_inventory_detects_duplicate_sha256_and_source_id_overlap(tmp_path) -> None:
    data_dir = tmp_path / "data"
    first = data_dir / "review" / "teacher_returns" / "batch_001_answers_rtl_answer_v0_1.json"
    second = data_dir / "review" / "repaired_rtl_answer_batches" / "teacher_returns" / "batch_001_answers_rtl_answer_v0_1.json"
    third = data_dir / "review" / "rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl"
    payload = {"answers": [_answer("shared_001"), _answer("shared_002")]}
    _write_json(first, payload)
    _write_json(second, payload)
    _write_jsonl(third, [_answer("shared_001")])

    result, code = collect_data_workspace_inventory(data_dir=data_dir)

    assert code == 0, result
    by_path = {entry["path"]: entry for entry in result["files"]}
    assert by_path["review/teacher_returns/batch_001_answers_rtl_answer_v0_1.json"]["duplicate_sha256"] is True
    assert by_path["review/repaired_rtl_answer_batches/teacher_returns/batch_001_answers_rtl_answer_v0_1.json"]["duplicate_sha256"] is True
    assert by_path["review/teacher_returns/batch_001_answers_rtl_answer_v0_1.json"]["source_id_overlap"] is True
    assert by_path["review/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl"]["source_id_overlap"] is True
    assert result["duplicate_sha256_group_count"] == 1
    assert result["overlapping_source_id_count"] >= 1


def test_inventory_writes_reports(tmp_path) -> None:
    data_dir = tmp_path / "data"
    output_md = data_dir / "reports" / "data_workspace_inventory.md"
    output_json = data_dir / "reports" / "data_workspace_inventory.json"
    _write_jsonl(data_dir / "review" / "tasks.jsonl", [_task("row_001")])

    result, code = collect_data_workspace_inventory(
        data_dir=data_dir,
        output_md=output_md,
        output_json=output_json,
    )

    assert code == 0, result
    assert output_md.exists()
    assert output_json.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["files_scanned"] == 1


def _write_v2(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_v2_inventory_classifies_known_and_unknown_paths(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_v2(data / "golden" / "golden.jsonl", '{"row": 1}\n')
    _write_v2(data / "raw" / "verilog_eval" / "upstream" / "README.md", "source\n")
    _write_v2(data / ".local_data" / "verilog-eval-main" / "dataset_spec-to-rtl" / "Prob001_ref.sv", "module ref; endmodule\n")
    _write_v2(data / "distill" / "v0.1" / "train.jsonl", "{}\n")
    _write_v2(data / "unknown" / "mystery.bin", "private\n")

    result = build_inventory(data)
    by_path = {entry["path"]: entry for entry in result["entries"]}
    assert by_path["data/golden"]["category"] == "reviewed_seed"
    assert by_path["data/raw/verilog_eval/upstream/README.md"]["category"] == "raw_source"
    assert by_path["data/.local_data/verilog-eval-main/dataset_spec-to-rtl"]["legacy"] is True
    assert by_path["data/distill/v0.1/train.jsonl"]["category"] == "distill_package"
    assert "data/unknown/mystery.bin" in result["unknown_paths"]
    assert all(not value.startswith("/") for value in result["unknown_paths"])
    assert "module ref; endmodule" not in json.dumps(result)


def test_v2_inventory_digests_and_hard_links_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "one" / "data"
    second = tmp_path / "two" / "data"
    _write_v2(first / "raw" / "a.txt", "same\n")
    _write_v2(second / "raw" / "a.txt", "same\n")
    os.link(first / "raw" / "a.txt", first / "raw" / "alias.txt")
    os.link(second / "raw" / "a.txt", second / "raw" / "alias.txt")
    left = build_inventory(first)
    right = build_inventory(second)
    left_by = {item["path"]: item for item in left["entries"]}
    assert left_by["data/raw/a.txt"]["sha256"] == hashlib.sha256(b"same\n").hexdigest()
    assert left_by["data/raw/a.txt"]["hard_link_count"] == 2
    assert left["summary"]["hard_link_file_count"] == 2
    assert json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def test_v2_inventory_rejects_symlinks_and_output_collisions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _write_v2(data / "raw" / "file.txt", "x")
    link = data / "raw" / "link.txt"
    link.symlink_to(data / "raw" / "file.txt")
    with pytest.raises(WorkspaceError, match="symlink"):
        build_inventory(data)

    link.unlink()
    output = tmp_path / "inventory.json"
    inventory_data_workspace(data, output)
    with pytest.raises(WorkspaceError, match="already exists"):
        inventory_data_workspace(data, output)
    inventory_data_workspace(data, output, force=True)
