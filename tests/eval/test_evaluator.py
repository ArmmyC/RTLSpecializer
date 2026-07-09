from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.teacher_distill import prepare_teacher_distill_dataset
from scripts.eval.evaluator import evaluate_answer, evaluate_dataset, load_candidate_answers
from scripts.eval.make_baseline_candidates import make_baseline_answer, make_candidates
from tests.dataset.conftest import GOLDEN, ROOT, write_rows
from tests.dataset.test_prepare_teacher_distill_dataset import _answer as _distill_answer
from tests.dataset.test_prepare_teacher_distill_dataset import _task as _distill_task


@pytest.fixture
def valid_row() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8").splitlines()[0])


def _candidate(row: dict, answer: dict | None = None) -> dict:
    return {"id": row["id"], "answer": deepcopy(answer or row["messages"][2]["content"]), "metadata": {"model": "test"}}


def _teacher_distill_row(tmp_path: Path) -> dict:
    source_id = "rtlcoder_resyn27k_000001_reference_synthetic_wrong_reset_polarity"
    task = _distill_task(source_id, candidate=True)
    task["source_dataset"] = "rtlcoder_resyn27k"
    task["license"] = "unconfirmed_upstream_license"
    task["provenance"]["origin"] = "external_rtlcoder_gpt_generated_unverified"
    task["provenance"]["public_dataset_name"] = "RTLCoder Resyn27k"
    task["provenance"]["public_dataset_url"] = None
    task["synthetic_bug"] = True
    answer = _distill_answer(source_id, candidate=True)
    answer["schema_version"] = "rtl_answer.v0.1"
    tasks_path = tmp_path / "tasks.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    output_dir = tmp_path / "distill"
    write_jsonl(tasks_path, [task])
    write_jsonl(answers_path, [answer])
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
    rows, problems = load_jsonl(output_dir / "test.jsonl")
    assert not problems, [problem.message for problem in problems]
    return rows[0][1]


def test_candidate_loader_accepts_valid_jsonl(tmp_path, valid_row) -> None:
    path = tmp_path / "candidates.jsonl"
    write_jsonl(path, [_candidate(valid_row)])
    result = load_candidate_answers(path)
    assert not result.errors
    assert valid_row["id"] in result.candidates


def test_duplicate_candidate_ids_fail(tmp_path, valid_row) -> None:
    path = tmp_path / "candidates.jsonl"
    write_jsonl(path, [_candidate(valid_row), _candidate(valid_row)])
    result = load_candidate_answers(path)
    assert result.duplicates == [valid_row["id"]]
    assert any("duplicate candidate id" in error for error in result.errors)


def test_missing_and_extra_candidates_are_reported(tmp_path, valid_row) -> None:
    extra = deepcopy(valid_row)
    extra["id"] = "extra_candidate"
    summary, code = evaluate_dataset([valid_row], {extra["id"]: {"answer": extra["messages"][2]["content"]}}, 1, tmp_path / "run", tmp_path / "dataset.jsonl", tmp_path / "candidates.jsonl")
    assert code == 0
    assert summary["missing_candidates"] == 1
    assert summary["extra_candidates"] == 1
    unmatched = load_jsonl(tmp_path / "run" / "unmatched_candidates.jsonl")[0]
    assert unmatched[0][1]["id"] == "extra_candidate"


def test_structurally_invalid_candidate_scores_low(tmp_path, valid_row) -> None:
    bad = {"schema_version": "rtl_answer_v0.1", "task_type": valid_row["task_family"]}
    result = evaluate_answer(valid_row, bad)
    assert result.score < 0.3
    assert result.errors


def test_unsafe_power_claim_creates_safety_failure(valid_row) -> None:
    answer = deepcopy(valid_row["messages"][2]["content"])
    answer["safe_optimization"]["expected_effect"] = "This reduces power."
    answer["claim_levels"]["power"] = "suggestion_only"
    result = evaluate_answer(valid_row, answer)
    assert result.safety_failures
    assert result.subscores["claim_safety"] < 0.20


def test_reference_candidate_scores_high(valid_row) -> None:
    result = evaluate_answer(valid_row, valid_row["messages"][2]["content"])
    assert result.score >= 0.85, result


def test_weak_baseline_scores_lower_than_reference(valid_row) -> None:
    reference = evaluate_answer(valid_row, valid_row["messages"][2]["content"])
    baseline = evaluate_answer(valid_row, make_baseline_answer(valid_row))
    assert baseline.score < reference.score


def test_metrics_include_breakdowns(tmp_path, valid_row) -> None:
    summary, code = evaluate_dataset([valid_row], {valid_row["id"]: {"answer": valid_row["messages"][2]["content"]}}, 1, tmp_path / "run", tmp_path / "dataset.jsonl", tmp_path / "candidates.jsonl")
    assert code == 0, summary
    metrics = json.loads((tmp_path / "run" / "metrics.json").read_text(encoding="utf-8"))
    assert valid_row["task_family"] in metrics["score_by_task_type"]
    assert valid_row["source"] in metrics["score_by_source"]
    assert valid_row["design_family"] in metrics["score_by_design_family"]


def test_evaluate_cli_json_output_is_parseable(tmp_path, valid_row) -> None:
    dataset = write_rows(tmp_path / "dataset.jsonl", [valid_row])
    candidates = tmp_path / "candidates.jsonl"
    write_jsonl(candidates, [_candidate(valid_row)])
    completed = subprocess.run(
        [
            sys.executable, "scripts/eval/evaluate_answers.py",
            "--dataset", str(dataset),
            "--candidates", str(candidates),
            "--output-dir", str(tmp_path / "run"),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["matched_rows"] == 1


def test_baseline_generator_creates_valid_candidate_jsonl(tmp_path, valid_row) -> None:
    dataset = write_rows(tmp_path / "dataset.jsonl", [valid_row])
    output = tmp_path / "baseline.jsonl"
    result, code = make_candidates(dataset, output)
    assert code == 0, result
    loaded = load_candidate_answers(output)
    assert not loaded.errors
    eval_result = evaluate_answer(valid_row, loaded.candidates[valid_row["id"]]["answer"])
    assert eval_result.score > 0.0


def test_baseline_cli_json_output_is_parseable(tmp_path, valid_row) -> None:
    dataset = write_rows(tmp_path / "dataset.jsonl", [valid_row])
    output = tmp_path / "baseline.jsonl"
    completed = subprocess.run(
        [
            sys.executable, "scripts/eval/make_baseline_candidates.py",
            "--dataset", str(dataset),
            "--output", str(output),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["candidate_rows"] == 1


def test_reference_candidate_accepts_dotted_answer_schema_in_teacher_distill_row(tmp_path) -> None:
    row = _teacher_distill_row(tmp_path)
    result = evaluate_answer(row, row["messages"][2]["content"])
    assert not any("invalid schema_version" in error for error in result.errors), result
    assert any("patch" in error for error in result.errors), result


def test_baseline_answer_is_valid_for_teacher_distill_rtlcoder_row(tmp_path) -> None:
    row = _teacher_distill_row(tmp_path)
    answer = make_baseline_answer(row)
    assert answer["source_id"] == row["source_id"]
    assert "tool_checks" in answer["evidence_used"]
    assert answer["limitations"]
    result = evaluate_answer(row, answer)
    assert not result.errors, result
    assert not result.safety_failures, result
