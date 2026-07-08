from __future__ import annotations

import json
from pathlib import Path

from scripts.eval.compare_rtl_eval_runs import compare_rtl_eval_runs


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _row(source_id: str, **categories) -> dict:
    return {
        "source_id": source_id,
        "model": "fixture-model",
        "parse_status": "parsed_json",
        "categories": categories,
        "errors": [],
        "warnings": [],
    }


def test_comparison_reports_improvements_and_regressions_correctly(tmp_path) -> None:
    baseline = {
        "category_counts": {
            "overall_valid": {"passed": 1, "failed": 1, "total": 2, "rate": 0.5},
            "json_valid": {"passed": 1, "failed": 1, "total": 2, "rate": 0.5},
            "schema_valid": {"passed": 1, "failed": 1, "total": 2, "rate": 0.5},
            "claim_safety_valid": {"passed": 1, "failed": 1, "total": 2, "rate": 0.5},
            "source_id_match": {"passed": 2, "failed": 0, "total": 2, "rate": 1.0},
            "reference_only_behavior_valid": {"passed": 2, "failed": 0, "total": 2, "rate": 1.0},
            "candidate_bug_behavior_valid": {"passed": 0, "failed": 0, "total": 0, "rate": None},
        },
        "row_results": [
            _row(
                "row_a",
                overall_valid=False,
                json_valid=False,
                schema_valid=False,
                claim_safety_valid=False,
                source_id_match=True,
                reference_only_behavior_valid=True,
                candidate_bug_behavior_valid=None,
            ),
            _row(
                "row_b",
                overall_valid=True,
                json_valid=True,
                schema_valid=True,
                claim_safety_valid=True,
                source_id_match=True,
                reference_only_behavior_valid=True,
                candidate_bug_behavior_valid=None,
            ),
        ],
        "failures_by_source_id": {"row_a": ["baseline failure"]},
    }
    finetuned = {
        "category_counts": {
            "overall_valid": {"passed": 1, "failed": 1, "total": 2, "rate": 0.5},
            "json_valid": {"passed": 2, "failed": 0, "total": 2, "rate": 1.0},
            "schema_valid": {"passed": 2, "failed": 0, "total": 2, "rate": 1.0},
            "claim_safety_valid": {"passed": 1, "failed": 1, "total": 2, "rate": 0.5},
            "source_id_match": {"passed": 2, "failed": 0, "total": 2, "rate": 1.0},
            "reference_only_behavior_valid": {"passed": 2, "failed": 0, "total": 2, "rate": 1.0},
            "candidate_bug_behavior_valid": {"passed": 0, "failed": 0, "total": 0, "rate": None},
        },
        "row_results": [
            _row(
                "row_a",
                overall_valid=True,
                json_valid=True,
                schema_valid=True,
                claim_safety_valid=True,
                source_id_match=True,
                reference_only_behavior_valid=True,
                candidate_bug_behavior_valid=None,
            ),
            _row(
                "row_b",
                overall_valid=False,
                json_valid=True,
                schema_valid=True,
                claim_safety_valid=False,
                source_id_match=True,
                reference_only_behavior_valid=True,
                candidate_bug_behavior_valid=None,
            ),
        ],
        "failures_by_source_id": {"row_b": ["finetuned failure"]},
    }
    baseline_path = tmp_path / "baseline.json"
    finetuned_path = tmp_path / "finetuned.json"
    output_md = tmp_path / "comparison.md"
    output_json = tmp_path / "comparison.json"
    _write_json(baseline_path, baseline)
    _write_json(finetuned_path, finetuned)
    summary, code = compare_rtl_eval_runs(
        baseline_path=baseline_path,
        finetuned_path=finetuned_path,
        output_md=output_md,
        output_json=output_json,
    )
    assert code == 0, summary
    assert summary["metric_deltas"]["json_valid"]["delta"] == 0.5
    assert summary["improvements"]["overall_valid"] == ["row_a"]
    assert summary["regressions"]["overall_valid"] == ["row_b"]
    assert summary["regressions"]["claim_safety_valid"] == ["row_b"]
    written = json.loads(output_json.read_text(encoding="utf-8"))
    assert written["improvements"]["json_valid"] == ["row_a"]
    assert "row_a" in output_md.read_text(encoding="utf-8")
    assert "row_b" in output_md.read_text(encoding="utf-8")
