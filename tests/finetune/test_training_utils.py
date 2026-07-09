from __future__ import annotations

import json
from pathlib import Path

from scripts.finetune.training_utils import (
    collect_environment_summary,
    format_example_for_chat_template,
    normalize_chat_messages,
    preflight_training_run,
)


def _row(source_id: str, split: str, *, user_schema: str = "rtl_task_v0.1", assistant_schema: str = "rtl_answer_v0.1") -> dict:
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
                    "schema_version": user_schema,
                    "prompt": f"Review {source_id}.",
                    "source_id": source_id,
                    "task_type": "rtl_bug_review",
                },
            },
            {
                "role": "assistant",
                "content": {
                    "schema_version": assistant_schema,
                    "issue_summary": [],
                    "source_id": source_id,
                    "task_type": "rtl_bug_review",
                },
            },
        ],
    }


def _write_dataset_dir(tmp_path: Path, *, canonical: bool = True) -> Path:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    user_schema = "rtl_task_v0.1" if canonical else "rtl_task.v0.1"
    assistant_schema = "rtl_answer_v0.1" if canonical else "rtl_answer.v0.1"
    for split_name in ("train", "validation", "test"):
        row = _row(split_name, split_name, user_schema=user_schema, assistant_schema=assistant_schema)
        path = dataset_dir / f"{split_name}.jsonl"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    return dataset_dir


class _DummyTokenizer:
    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False
        assert add_generation_prompt is False
        return "\n".join(f"{message['role']}={message['content']}" for message in messages)


def test_normalize_chat_messages_stringifies_structured_content() -> None:
    normalized = normalize_chat_messages([
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": {"b": 2, "a": 1}},
    ])
    assert normalized == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": '{"a":1,"b":2}'},
    ]


def test_format_example_for_chat_template_uses_stringified_messages() -> None:
    example = _row("fixture", "train")
    rendered = format_example_for_chat_template(example, _DummyTokenizer())
    assert "system=You are an RTL review specialist." in rendered
    assert '"schema_version":"rtl_task_v0.1"' in rendered
    assert '"schema_version":"rtl_answer_v0.1"' in rendered


def test_preflight_accepts_canonical_dataset(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path, canonical=True)
    output_dir = tmp_path / "output"
    summary, code = preflight_training_run(dataset_dir, output_dir)
    assert code == 0, summary
    assert summary["ok"] is True
    assert summary["dataset_check"]["schema_aliases"] == {"user": {}, "assistant": {}}


def test_preflight_rejects_alias_carrying_dataset(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path, canonical=False)
    output_dir = tmp_path / "output"
    summary, code = preflight_training_run(dataset_dir, output_dir)
    assert code == 1
    assert any("canonical fine-tune export" in error for error in summary["errors"])


def test_preflight_rejects_nonempty_output_without_overwrite(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path, canonical=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "stale.txt").write_text("keep\n", encoding="utf-8")
    summary, code = preflight_training_run(dataset_dir, output_dir)
    assert code == 1
    assert any("--output-dir already exists and is not empty" in error for error in summary["errors"])


def test_collect_environment_summary_flags_missing_dataset_without_package_imports(tmp_path) -> None:
    def fake_importer(name: str):
        class _TorchCuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def is_bf16_supported() -> bool:
                return True

            @staticmethod
            def device_count() -> int:
                return 1

            @staticmethod
            def get_device_name(index: int) -> str:
                return "NVIDIA L40"

            @staticmethod
            def get_device_properties(index: int):
                class _Props:
                    total_memory = 48 * 1024**3

                return _Props()

        class _Module:
            __version__ = "1.0"
            cuda = _TorchCuda()

        return _Module()

    missing_dir = tmp_path / "missing"
    summary, code = collect_environment_summary(
        dataset_dir=missing_dir,
        expected_gpu_substring="L40",
        importer=fake_importer,
    )
    assert code == 1
    assert summary["packages"]["torch"] == "1.0"
    assert summary["torch"]["cuda_available"] is True
    assert any("dataset directory not found" in error for error in summary["errors"])
