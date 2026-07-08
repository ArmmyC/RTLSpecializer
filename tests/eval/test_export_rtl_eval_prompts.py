from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.teacher_distill import prepare_teacher_distill_dataset
from scripts.eval.export_rtl_eval_prompts import export_rtl_eval_prompts
from tests.dataset.test_prepare_teacher_distill_dataset import _answer, _task


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    rows, problems = load_jsonl(path)
    assert not problems, [problem.message for problem in problems]
    return [row for _, row in rows]


def _prepare_test_split(
    tmp_path: Path,
    *,
    source_id: str = "Prob200_ref_a",
    candidate: bool = False,
    answer_marker: str | None = None,
) -> Path:
    task = _task(source_id, candidate=candidate)
    answer = _answer(source_id, candidate=candidate)
    if answer_marker is not None:
        answer["limitations"] = [answer_marker]
    tasks_path = tmp_path / "tasks.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    output_dir = tmp_path / "distill"
    _write_jsonl(tasks_path, [task])
    _write_jsonl(answers_path, [answer])
    result, code = prepare_teacher_distill_dataset(
        tasks_path=tasks_path,
        answers_path=answers_path,
        output_dir=output_dir,
        train_size=0,
        validation_size=0,
        test_size=1,
        seed=42,
        strict=True,
    )
    assert code == 0, result
    return output_dir / "test.jsonl"


def test_prompt_export_removes_expected_answer_from_prompt(tmp_path) -> None:
    marker = "UNIQUE_EXPECTED_ANSWER_MARKER"
    dataset_path = _prepare_test_split(tmp_path, answer_marker=marker)
    output_path = tmp_path / "prompts.jsonl"
    result, code = export_rtl_eval_prompts(dataset_path, output_path, split="test", strict=True)
    assert code == 0, result
    exported = _read_jsonl(output_path)[0]
    prompt_text = json.dumps(exported["prompt_messages"], ensure_ascii=False, sort_keys=True)
    assert marker not in prompt_text
    assert exported["prompt_messages"][0]["role"] == "system"
    assert exported["prompt_messages"][1]["role"] == "user"
    assert json.loads(exported["user_prompt"]) == exported["user_content"]


def test_prompt_export_preserves_expected_answer_separately(tmp_path) -> None:
    marker = "UNIQUE_EXPECTED_ANSWER_MARKER"
    dataset_path = _prepare_test_split(tmp_path, answer_marker=marker)
    output_path = tmp_path / "prompts.jsonl"
    result, code = export_rtl_eval_prompts(dataset_path, output_path, split="test", strict=True)
    assert code == 0, result
    exported = _read_jsonl(output_path)[0]
    assert exported["user_content"]["schema_version"] == "rtl_task_v0.1"
    assert exported["expected_answer"]["schema_version"] == "rtl_answer_v0.1"
    assert exported["expected_answer"]["limitations"] == [marker]
    assert exported["expected_answer"]["source_id"] == exported["source_id"]


def test_malformed_role_order_fails(tmp_path) -> None:
    dataset_path = _prepare_test_split(tmp_path)
    row = _read_jsonl(dataset_path)[0]
    row["messages"] = [row["messages"][1], row["messages"][0], row["messages"][2]]
    bad_input = tmp_path / "bad.jsonl"
    _write_jsonl(bad_input, [row])
    output_path = tmp_path / "prompts.jsonl"
    result, code = export_rtl_eval_prompts(bad_input, output_path, split="test", strict=True)
    assert code == 1
    assert result["ok"] is False
    assert result["exported_rows"] == 0
    assert any("role" in message.lower() or "system/user/assistant" in message for message in result["errors"])
    assert not output_path.exists()
