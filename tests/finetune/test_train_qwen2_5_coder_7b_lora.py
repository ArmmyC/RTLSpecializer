from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.finetune.train_qwen2_5_coder_7b_lora import train_qwen2_5_coder_7b_lora


def _write_dataset_dir(tmp_path: Path) -> Path:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    row = {
        "id": "teacher_distill_fixture",
        "dataset_version": "dataset_v0.1",
        "split": "train",
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
        "source_id": "fixture",
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
                    "prompt": "Review the design.",
                    "source_id": "fixture",
                    "task_type": "rtl_bug_review",
                },
            },
            {
                "role": "assistant",
                "content": {
                    "schema_version": "rtl_answer_v0.1",
                    "issue_summary": [],
                    "source_id": "fixture",
                    "task_type": "rtl_bug_review",
                },
            },
        ],
    }
    for split_name in ("train", "validation", "test"):
        split_row = dict(row)
        split_row["split"] = split_name
        split_row["id"] = f"teacher_distill_{split_name}"
        split_path = dataset_dir / f"{split_name}.jsonl"
        split_path.write_text(json.dumps(split_row, ensure_ascii=False) + "\n", encoding="utf-8")
    return dataset_dir


def test_dry_run_returns_plan_without_runtime_imports(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    args = argparse.Namespace(
        base_model="Qwen/Qwen2.5-Coder-7B-Instruct",
        dataset_dir=dataset_dir,
        output_dir=tmp_path / "out",
        resume_from_checkpoint=None,
        overwrite_output_dir=False,
        max_length=4096,
        learning_rate=1.0e-4,
        epochs=1.0,
        batch_size=1,
        eval_batch_size=1,
        gradient_accumulation_steps=16,
        logging_steps=10,
        save_steps=100,
        eval_steps=100,
        save_total_limit=2,
        seed=42,
        lora_r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        expected_gpu_substring="L40",
        dry_run=True,
        json=True,
    )
    summary, code = train_qwen2_5_coder_7b_lora(args)
    assert code == 0, summary
    assert summary["ok"] is True
    assert summary["mode"] == "dry_run"
    assert summary["environment"] is None
    assert summary["preflight"]["dataset_check"]["total_rows"] == 3


def test_dry_run_propagates_preflight_errors(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "existing.txt").write_text("present\n", encoding="utf-8")
    args = argparse.Namespace(
        base_model="Qwen/Qwen2.5-Coder-7B-Instruct",
        dataset_dir=dataset_dir,
        output_dir=output_dir,
        resume_from_checkpoint=None,
        overwrite_output_dir=False,
        max_length=4096,
        learning_rate=1.0e-4,
        epochs=1.0,
        batch_size=1,
        eval_batch_size=1,
        gradient_accumulation_steps=16,
        logging_steps=10,
        save_steps=100,
        eval_steps=100,
        save_total_limit=2,
        seed=42,
        lora_r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        expected_gpu_substring="L40",
        dry_run=True,
        json=True,
    )
    summary, code = train_qwen2_5_coder_7b_lora(args)
    assert code == 1
    assert summary["ok"] is False
    assert any("--output-dir already exists and is not empty" in error for error in summary["errors"])
