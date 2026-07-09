from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from scripts.dataset.io_utils import write_jsonl
from scripts.eval.inspect_candidate_differences import (
    analyze_duplicate_answers,
    inspect_candidate_differences,
)
from scripts.eval.make_baseline_candidates import make_baseline_answer
from tests.dataset.conftest import GOLDEN


def _rows() -> list[dict]:
    loaded = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()[:2]]
    for index, row in enumerate(loaded, 1):
        task = row["messages"][1]["content"]
        task["source_id"] = f"fixture_row_{index}_synthetic_wrong_reset_polarity"
        task["mutation_summary"] = "Synthetic bug changed reset polarity on rst_n."
        task["mutated_signal_names"] = ["rst_n"]
        row["source_id"] = task["source_id"]
    return loaded


def _dataset(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "dataset.jsonl"
    write_jsonl(path, rows)
    return path


def _candidate_row(row: dict, answer: dict) -> dict:
    return {"id": row["id"], "answer": answer, "metadata": {"model": "fixture"}}


def test_candidate_diff_detects_changed_issue_text(tmp_path) -> None:
    rows = _rows()
    dataset = _dataset(tmp_path, rows)
    answer_a = make_baseline_answer(rows[0])
    answer_b = deepcopy(answer_a)
    answer_b["issue_summary"][0]["issue"] = "Reset polarity appears inverted in the supplied RTL."
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    write_jsonl(path_a, [_candidate_row(rows[0], answer_a)])
    write_jsonl(path_b, [_candidate_row(rows[0], answer_b)])
    summary, code = inspect_candidate_differences(
        dataset=dataset,
        candidates_a=path_a,
        name_a="rule",
        candidates_b=path_b,
        name_b="active",
        output_md=tmp_path / "diff.md",
        output_json=tmp_path / "diff.json",
    )
    assert code == 0, summary
    assert summary["top_differences"][0]["id"] == rows[0]["id"]
    assert summary["top_differences"][0]["issue_summary_text"]["rule"] != summary["top_differences"][0]["issue_summary_text"]["active"]
    assert summary["top_differences"][0]["difference_score"] >= 1


def test_duplicate_generic_answer_detector_works(tmp_path) -> None:
    rows = _rows()
    answer = make_baseline_answer(rows[0])
    candidates = {
        rows[0]["id"]: {"answer": answer},
        rows[1]["id"]: {"answer": deepcopy(answer)},
    }
    duplicates = analyze_duplicate_answers(candidates)
    assert duplicates["exact_duplicate_groups"]
    assert duplicates["near_duplicate_pairs"]


def test_mutation_mention_detection_works(tmp_path) -> None:
    rows = _rows()
    dataset = _dataset(tmp_path, rows)
    answer_a = make_baseline_answer(rows[0])
    answer_b = deepcopy(answer_a)
    answer_b["issue_summary"][0]["issue"] = "The wrong reset polarity on rst_n may invert reset behavior."
    answer_b["issue_summary"][0]["evidence"]["reason"] = (
        "The synthetic wrong reset polarity change on rst_n appears in text inspection of the supplied RTL."
    )
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    write_jsonl(path_a, [_candidate_row(rows[0], answer_a)])
    write_jsonl(path_b, [_candidate_row(rows[0], answer_b)])
    summary, code = inspect_candidate_differences(
        dataset=dataset,
        candidates_a=path_a,
        name_a="rule",
        candidates_b=path_b,
        name_b="active",
        output_md=tmp_path / "diff.md",
        output_json=tmp_path / "diff.json",
    )
    assert code == 0, summary
    top = summary["top_differences"][0]
    assert top["mentions_mutation_type"]["rule"] is False
    assert top["mentions_mutation_type"]["active"] is True
    assert top["mentions_mutated_signal_names"]["active"] is True
