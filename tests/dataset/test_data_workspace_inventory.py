from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.data_workspace import collect_data_workspace_inventory


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
