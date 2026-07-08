from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.teacher_distill import prepare_teacher_distill_dataset
from scripts.eval.export_rtl_eval_prompts import export_rtl_eval_prompts
from scripts.eval.score_rtl_answer_json import score_rtl_answer_json
from tests.dataset.test_prepare_teacher_distill_dataset import _answer, _task


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    rows, problems = load_jsonl(path)
    assert not problems, [problem.message for problem in problems]
    return [row for _, row in rows]


def _export_prompts(
    tmp_path: Path,
    *,
    source_id: str,
    candidate: bool,
) -> tuple[Path, dict]:
    task = _task(source_id, candidate=candidate)
    answer = _answer(source_id, candidate=candidate)
    tasks_path = tmp_path / "tasks.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    distill_dir = tmp_path / "distill"
    _write_jsonl(tasks_path, [task])
    _write_jsonl(answers_path, [answer])
    result, code = prepare_teacher_distill_dataset(
        tasks_path=tasks_path,
        answers_path=answers_path,
        output_dir=distill_dir,
        train_size=0,
        validation_size=0,
        test_size=1,
        seed=42,
        strict=True,
    )
    assert code == 0, result
    prompts_path = tmp_path / "prompts.jsonl"
    export_result, export_code = export_rtl_eval_prompts(
        distill_dir / "test.jsonl",
        prompts_path,
        split="test",
        strict=True,
    )
    assert export_code == 0, export_result
    prompt_row = _read_jsonl(prompts_path)[0]
    return prompts_path, prompt_row


def _score_predictions(tmp_path: Path, prompts_path: Path, predictions: list[dict], *, strict: bool = True) -> tuple[dict, int]:
    predictions_path = tmp_path / "predictions.jsonl"
    output_json = tmp_path / "scores.json"
    output_md = tmp_path / "scores.md"
    _write_jsonl(predictions_path, predictions)
    return score_rtl_answer_json(
        prompts_path=prompts_path,
        predictions_path=predictions_path,
        output_json=output_json,
        output_md=output_md,
        strict=strict,
    )


def test_scoring_accepts_valid_rtl_answer_output(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob200_ref_a", candidate=False)
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": prompt_row["expected_answer"],
    }])
    assert code == 0, summary
    row_result = summary["row_results"][0]
    assert row_result["categories"]["json_valid"] is True
    assert row_result["categories"]["schema_valid"] is True
    assert row_result["categories"]["overall_valid"] is True


def test_scoring_rejects_invalid_json(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob200_ref_a", candidate=False)
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": "not json",
    }])
    assert code == 1
    row_result = summary["row_results"][0]
    assert row_result["categories"]["json_valid"] is False
    assert row_result["categories"]["overall_valid"] is False


def test_scoring_rejects_wrong_schema_version(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob200_ref_a", candidate=False)
    bad_answer = dict(prompt_row["expected_answer"])
    bad_answer["schema_version"] = "rtl_answer_v9.9"
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": bad_answer,
    }])
    assert code == 1
    row_result = summary["row_results"][0]
    assert row_result["categories"]["schema_valid"] is False


def test_scoring_rejects_source_id_mismatch(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob200_ref_a", candidate=False)
    bad_answer = dict(prompt_row["expected_answer"])
    bad_answer["source_id"] = "Prob999_wrong"
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": bad_answer,
    }])
    assert code == 1
    row_result = summary["row_results"][0]
    assert row_result["categories"]["source_id_match"] is False


def test_scoring_rejects_unsupported_claims_without_evidence(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob200_ref_a", candidate=False)
    unsafe_answer = json.loads(json.dumps(prompt_row["expected_answer"]))
    unsafe_answer["limitations"] = ["Passed simulation and synthesis passed with lower power."]
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": unsafe_answer,
    }])
    assert code == 1
    row_result = summary["row_results"][0]
    assert row_result["categories"]["claim_safety_valid"] is False


def test_scoring_rejects_reference_only_rows_that_invent_candidate_bugs(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob200_ref_a", candidate=False)
    invented_bug_answer = json.loads(json.dumps(prompt_row["expected_answer"]))
    invented_bug_answer["issue_summary"][0]["issue"] = "Candidate DUT bug causes an incorrect output width."
    invented_bug_answer["issue_summary"][0]["evidence"]["reason"] = "Candidate DUT bug inferred from prompt."
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": invented_bug_answer,
    }])
    assert code == 1
    row_result = summary["row_results"][0]
    assert row_result["categories"]["reference_only_behavior_valid"] is False


def test_scoring_can_identify_candidate_bug_rows(tmp_path) -> None:
    prompts_path, prompt_row = _export_prompts(tmp_path, source_id="Prob062_bugs_mux2", candidate=True)
    summary, code = _score_predictions(tmp_path, prompts_path, [{
        "source_id": prompt_row["source_id"],
        "model": "fixture-model",
        "output": prompt_row["expected_answer"],
    }])
    assert code == 0, summary
    row_result = summary["row_results"][0]
    assert row_result["categories"]["candidate_bug_behavior_valid"] is True
    assert row_result["categories"]["overall_valid"] is True

