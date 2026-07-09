from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.io_utils import write_jsonl
from scripts.eval.compare_eval_runs import compare_eval_runs


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _row(row_id: str, score: float, *, errors: list[str] | None = None, safety_failures: list[str] | None = None) -> dict:
    return {
        "id": row_id,
        "score": score,
        "subscores": {},
        "errors": errors or [],
        "warnings": [],
        "safety_failures": safety_failures or [],
    }


def _run_dir(tmp_path: Path, name: str, rows: list[dict], *, mean_score: float | None = None) -> Path:
    run_dir = tmp_path / name
    write_jsonl(run_dir / "row_results.jsonl", rows)
    metrics = {
        "dataset_rows": len(rows),
        "candidate_rows": len(rows),
        "matched_rows": len(rows),
        "missing_candidates": 0,
        "extra_candidates": 0,
        "mean_score": mean_score if mean_score is not None else round(sum(row["score"] for row in rows) / len(rows), 6),
        "median_score": sorted(row["score"] for row in rows)[len(rows) // 2],
        "min_score": min(row["score"] for row in rows),
        "max_score": max(row["score"] for row in rows),
        "score_by_task_type": {"rtl_bug_review": round(sum(row["score"] for row in rows) / len(rows), 6)},
        "score_by_source": {},
        "score_by_design_family": {},
        "safety_failure_counts": {},
        "safety_failures": sum(len(row["safety_failures"]) for row in rows),
        "error_counts": {"example": 1 if any(row["errors"] for row in rows) else 0},
    }
    _write_json(run_dir / "metrics.json", metrics)
    return run_dir


def test_comparison_handles_two_eval_runs(tmp_path) -> None:
    run_a = _run_dir(tmp_path, "run_a", [_row("row_1", 0.2), _row("row_2", 0.7)])
    run_b = _run_dir(tmp_path, "run_b", [_row("row_1", 0.8), _row("row_2", 0.6)])
    summary, code = compare_eval_runs(
        [run_a, run_b],
        tmp_path / "comparison.md",
        tmp_path / "comparison.json",
    )
    assert code == 0, summary
    assert summary["ok"] is True
    assert summary["runs"][0]["name"] == "run_a"
    assert summary["runs"][1]["name"] == "run_b"
    assert summary["largest_score_differences"][0]["id"] == "row_1"
    assert summary["largest_score_differences"][0]["score_spread"] == 0.6


def test_missing_metrics_is_reported_clearly(tmp_path) -> None:
    run_a = _run_dir(tmp_path, "run_a", [_row("row_1", 0.2)])
    run_b = tmp_path / "run_b"
    run_b.mkdir()
    write_jsonl(run_b / "row_results.jsonl", [_row("row_1", 0.8)])
    summary, code = compare_eval_runs(
        [run_a, run_b],
        tmp_path / "comparison.md",
        tmp_path / "comparison.json",
    )
    assert code == 1
    assert any("metrics file not found" in error for error in summary["errors"])


def test_row_overlap_works(tmp_path) -> None:
    run_a = _run_dir(tmp_path, "run_a", [_row("row_1", 0.2), _row("row_2", 0.7)])
    run_b = _run_dir(tmp_path, "run_b", [_row("row_2", 0.6), _row("row_3", 0.9)])
    summary, code = compare_eval_runs(
        [run_a, run_b],
        tmp_path / "comparison.md",
        tmp_path / "comparison.json",
    )
    assert code == 0, summary
    assert summary["pairwise_overlap"]["run_a"]["run_b"] == 1
    assert summary["missing_by_run"]["run_a"] == ["row_3"]
    assert summary["missing_by_run"]["run_b"] == ["row_1"]
