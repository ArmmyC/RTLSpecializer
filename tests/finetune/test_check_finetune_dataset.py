from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from scripts.finetune.check_finetune_dataset import check_finetune_dataset


def _row(source_id: str, split: str) -> dict:
    return {
        "id": f"teacher_distill_{source_id}",
        "dataset_version": "dataset_v0.1",
        "split": split,
        "source": "teacher_generated",
        "license": "unconfirmed_upstream_license",
        "design_family": "rtlcoder_synthetic",
        "task_family": "rtl_bug_review",
        "created_by": "prepare_teacher_distill_dataset",
        "review_status": "teacher_distilled_unreviewed",
        "approval_status": "not_approved",
        "dataset_stage": "teacher_distill_pilot",
        "dataset_name": "rtlcoder_synthetic_teacher_distill_v0_1",
        "schema_pair": "rtl_task_v0.1_to_rtl_answer_v0.1",
        "promotion_allowed": False,
        "split_seed": 42,
        "source_family": "rtlcoder_resyn27k",
        "source_id": source_id,
        "provenance": {
            "origin": "external_rtlcoder_gpt_generated_unverified",
            "public_dataset_name": "RTLCoder Resyn27k",
            "public_dataset_url": None,
            "source_commit": None,
            "notes": "fixture",
        },
        "tool_checks": {
            "parse": None,
            "lint": None,
            "simulation": None,
            "equivalence": None,
            "synthesis": None,
            "toggle": None,
            "power": None,
        },
        "messages": [
            {"role": "system", "content": "You are an RTL review specialist."},
            {
                "role": "user",
                "content": {
                    "schema_version": "rtl_task_v0.1",
                    "source_id": source_id,
                    "task_type": "rtl_bug_review",
                    "prompt": f"Review {source_id}.",
                },
            },
            {
                "role": "assistant",
                "content": {
                    "schema_version": "rtl_answer.v0.1",
                    "source_id": source_id,
                    "task_type": "rtl_bug_review",
                    "issue_summary": [],
                },
            },
        ],
    }


def _write_dataset_dir(tmp_path: Path, *, rows_per_split: int = 2) -> Path:
    dataset_dir = tmp_path / "distill_dataset"
    dataset_dir.mkdir()
    for split_name in ("train", "validation", "test"):
        rows = [_row(f"{split_name}_{index}", split_name) for index in range(rows_per_split)]
        path = dataset_dir / f"{split_name}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return dataset_dir


def test_accepts_valid_3_split_teacher_distill_dataset(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    summary, code = check_finetune_dataset(dataset_dir)
    assert code == 0, summary
    assert summary["ok"] is True
    assert summary["splits"]["train"]["rows"] == 2
    assert summary["splits"]["validation"]["rows"] == 2
    assert summary["splits"]["test"]["rows"] == 2
    assert summary["schema_aliases"]["assistant"] == {"rtl_answer.v0.1": 6}


def test_fails_missing_split_file(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    (dataset_dir / "validation.jsonl").unlink()
    summary, code = check_finetune_dataset(dataset_dir)
    assert code == 1
    assert any("missing split file" in error for error in summary["errors"])


def test_detects_missing_assistant_message(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    train_path = dataset_dir / "train.jsonl"
    rows = [json.loads(line) for line in train_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["messages"] = rows[0]["messages"][:2]
    train_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary, code = check_finetune_dataset(dataset_dir)
    assert code == 1
    assert any("exactly three messages" in error for error in summary["errors"])


def test_detects_wrong_assistant_schema(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    test_path = dataset_dir / "test.jsonl"
    rows = [json.loads(line) for line in test_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["messages"][2]["content"]["schema_version"] = "rtl_answer_v9.9"
    test_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary, code = check_finetune_dataset(dataset_dir)
    assert code == 1
    assert any("assistant schema_version must be 'rtl_answer_v0.1'" in error for error in summary["errors"])


def test_reports_review_and_approval_distribution_and_writes_reports(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    output_md = tmp_path / "report.md"
    output_json = tmp_path / "report.json"
    summary, code = check_finetune_dataset(dataset_dir, output_md=output_md, output_json=output_json)
    assert code == 0, summary
    assert summary["review_status_distribution"] == {"teacher_distilled_unreviewed": 6}
    assert summary["approval_status_distribution"] == {"not_approved": 6}
    assert output_md.is_file()
    assert output_json.is_file()
    written = json.loads(output_json.read_text(encoding="utf-8"))
    assert written["total_rows"] == 6
    assert "Fine-tune dataset check" in output_md.read_text(encoding="utf-8")


def test_does_not_modify_data(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    before = {
        path.name: path.read_text(encoding="utf-8")
        for path in dataset_dir.glob("*.jsonl")
    }
    summary, code = check_finetune_dataset(dataset_dir)
    after = {
        path.name: path.read_text(encoding="utf-8")
        for path in dataset_dir.glob("*.jsonl")
    }
    assert code == 0, summary
    assert before == after
