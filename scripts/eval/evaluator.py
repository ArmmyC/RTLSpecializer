"""Deterministic answer evaluator for local dataset_v0.1 JSONL files."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import statistics
import tempfile
from typing import Any

from scripts.dataset.claim_safety import find_unsupported_claims
from scripts.dataset.constants import ANSWER_SCHEMA_VERSION, ANSWER_SCHEMA_VERSIONS, CLAIM_DOMAINS, CLAIM_LEVELS
from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.validation import (
    extract_module_names,
    has_passing_tool_evidence,
    has_tool_evidence,
    validate_dataset_file,
)


WEIGHTS = {
    "schema_and_required_fields": 0.20,
    "issue_grounding": 0.25,
    "reasoning_quality": 0.20,
    "claim_safety": 0.20,
    "verification_plan": 0.10,
    "task_alignment": 0.05,
}
REQUIRED_ANSWER_FIELDS = {
    "schema_version", "source_id", "task_type", "issue_summary", "time_reasoning",
    "space_reasoning", "safe_optimization", "functional_risk", "verification_plan",
    "claim_levels", "evidence_used", "limitations", "patch",
}
GENERIC_TEXT = ("requires review", "draft seed", "baseline", "no optimization effect", "not verified during import")


@dataclass(frozen=True)
class CandidateLoadResult:
    candidates: dict[str, dict[str, Any]]
    rows: int
    errors: list[str]
    warnings: list[str]
    duplicates: list[str]


@dataclass(frozen=True)
class RowEvalResult:
    id: str
    score: float
    subscores: dict[str, float]
    errors: list[str]
    warnings: list[str]
    safety_failures: list[str]


def load_candidate_answers(path: Path) -> CandidateLoadResult:
    loaded, problems = load_jsonl(path)
    errors = [problem.message for problem in problems]
    candidates: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for line, row in loaded:
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            errors.append(f"line {line}: id must be a non-empty string")
            continue
        if row_id in candidates:
            duplicates.append(row_id)
            errors.append(f"line {line}: duplicate candidate id {row_id}")
            continue
        answer = row.get("answer")
        if not isinstance(answer, dict):
            errors.append(f"line {line}: answer must be an object")
            continue
        candidates[row_id] = {"answer": answer, "metadata": row.get("metadata", {})}
    return CandidateLoadResult(candidates, len(loaded), errors, [], duplicates)


def _candidate_validation_errors(dataset_row: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    row = deepcopy(dataset_row)
    row["messages"][2]["content"] = answer
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.jsonl"
        write_jsonl(path, [row])
        report = validate_dataset_file(path, strict=True)
    return [item.format() for item in report.errors + report.warnings]


def _answer_text(answer: Any) -> str:
    return json.dumps(answer, sort_keys=True, ensure_ascii=False) if isinstance(answer, (dict, list)) else str(answer)


def _artifacts(dataset_row: dict[str, Any]) -> dict[str, Any]:
    return dataset_row["messages"][1]["content"].get("artifacts", {})


def _reference_answer(dataset_row: dict[str, Any]) -> dict[str, Any]:
    return dataset_row["messages"][2]["content"]


def _reference_signals(dataset_row: dict[str, Any]) -> set[str]:
    signals: set[str] = set()
    ref = _reference_answer(dataset_row)
    for issue in ref.get("issue_summary", []) if isinstance(ref.get("issue_summary"), list) else []:
        evidence = issue.get("evidence", {}) if isinstance(issue, dict) else {}
        for signal in evidence.get("signal_names", []) if isinstance(evidence, dict) else []:
            if isinstance(signal, str):
                signals.add(signal)
    summary = dataset_row["messages"][1]["content"].get("extracted_rtl_summary", {})
    for key in ("clock_signals", "reset_signals", "registered_signals", "suspected_fsm_signals", "suspected_counters", "activity_hotspots"):
        for signal in summary.get(key, []) if isinstance(summary, dict) else []:
            if isinstance(signal, str):
                signals.add(signal)
    return signals


def _top_modules(dataset_row: dict[str, Any]) -> set[str]:
    modules: set[str] = set()
    summary = dataset_row["messages"][1]["content"].get("extracted_rtl_summary", {})
    if isinstance(summary, dict) and isinstance(summary.get("top_module"), str):
        modules.add(summary["top_module"])
    for value in _artifacts(dataset_row).values():
        if isinstance(value, str):
            modules.update(extract_module_names(value))
    return {module.lower() for module in modules}


def _schema_score(dataset_row: dict[str, Any], answer: Any, errors: list[str]) -> float:
    if not isinstance(answer, dict):
        errors.append("candidate answer must be an object")
        return 0.0
    points = 0
    if not (missing := REQUIRED_ANSWER_FIELDS - answer.keys()):
        points += 2
    else:
        errors.append(f"missing answer fields: {sorted(missing)}")
    if answer.get("schema_version") in ANSWER_SCHEMA_VERSIONS:
        points += 1
    else:
        errors.append("invalid schema_version")
    if answer.get("task_type") == dataset_row["task_family"]:
        points += 1
    else:
        errors.append("task_type mismatch")
    levels = answer.get("claim_levels")
    if isinstance(levels, dict) and CLAIM_DOMAINS.issubset(levels.keys()) and all(levels.get(domain) in CLAIM_LEVELS for domain in CLAIM_DOMAINS):
        points += 1
    else:
        errors.append("invalid claim_levels")
    if isinstance(answer.get("patch"), dict):
        points += 1
    else:
        errors.append("patch must be an object")
    return WEIGHTS["schema_and_required_fields"] * points / 6


def _issue_grounding_score(dataset_row: dict[str, Any], answer: dict[str, Any], warnings: list[str]) -> float:
    issues = answer.get("issue_summary")
    if not isinstance(issues, list) or not issues or not isinstance(issues[0], dict):
        warnings.append("issue_summary missing or empty")
        return 0.0
    issue = issues[0]
    evidence = issue.get("evidence", {}) if isinstance(issue.get("evidence"), dict) else {}
    points = 0
    if isinstance(issue.get("issue"), str) and issue["issue"].strip():
        points += 1
    if issue.get("severity") in {"low", "medium", "high"}:
        points += 1
    signals = evidence.get("signal_names")
    if isinstance(signals, list) and any(isinstance(item, str) and item.strip() for item in signals):
        points += 1
    location = evidence.get("code_location", {}) if isinstance(evidence.get("code_location"), dict) else {}
    module = location.get("module")
    modules = _top_modules(dataset_row)
    if isinstance(module, str) and (not modules or module.lower() in modules):
        points += 1
    reason = evidence.get("reason")
    if isinstance(reason, str) and len(reason.strip()) >= 20 and not any(text in reason.lower() for text in GENERIC_TEXT):
        points += 1
    text = _answer_text(answer).lower()
    ref_signals = _reference_signals(dataset_row)
    artifact_fields = {key for key, value in _artifacts(dataset_row).items() if value}
    if any(signal.lower() in text for signal in ref_signals) or any(field.lower() in text for field in artifact_fields):
        points += 1
    return WEIGHTS["issue_grounding"] * points / 6


def _reasoning_score(answer: dict[str, Any]) -> float:
    points = 0
    time = answer.get("time_reasoning", {})
    space = answer.get("space_reasoning", {})
    if isinstance(time, dict) and isinstance(time.get("clock_cycle_behavior"), str) and len(time["clock_cycle_behavior"].strip()) >= 20 and not any(text in time["clock_cycle_behavior"].lower() for text in GENERIC_TEXT):
        points += 1
    if isinstance(time, dict) and time.get("latency_or_state_risk") and time.get("reset_behavior_risk"):
        points += 1
    resources = space.get("hardware_resources_involved") if isinstance(space, dict) else None
    if isinstance(resources, list) and resources:
        points += 1
    area = space.get("area_risk", "") if isinstance(space, dict) else ""
    activity = space.get("activity_risk", "") if isinstance(space, dict) else ""
    if re.search(r"evidence|synthesis|tool|report|unavailable", str(area), re.IGNORECASE):
        points += 1
    if re.search(r"evidence|vcd|toggle|activity|tool|report|unavailable", str(activity), re.IGNORECASE):
        points += 1
    return WEIGHTS["reasoning_quality"] * points / 5


def _claim_safety_score(dataset_row: dict[str, Any], answer: dict[str, Any], validation_errors: list[str]) -> tuple[float, list[str]]:
    failures = [message for _, message in find_unsupported_claims(dataset_row, answer)]
    failures.extend(error for error in validation_errors if "verified requires" in error or "tool_supported requires" in error or "unsupported" in error)
    penalty = min(WEIGHTS["claim_safety"], 0.05 * len(failures))
    return max(0.0, WEIGHTS["claim_safety"] - penalty), failures


def _verification_plan_score(answer: dict[str, Any]) -> float:
    plan = answer.get("verification_plan")
    text = " ".join(str(item).lower() for item in plan) if isinstance(plan, list) else ""
    answer_text = _answer_text(answer).lower()
    points = 0
    if "lint" in text or "compile" in text:
        points += 1
    if "simulation" in text or "simulate" in text:
        points += 1
    if "synthesis" in text or "area" not in answer_text:
        points += 1
    if "vcd" in text or "toggle" in text or "activity" not in answer_text:
        points += 1
    if "power" not in answer_text or "power report" in text:
        points += 1
    return WEIGHTS["verification_plan"] * points / 5


def _task_alignment_score(dataset_row: dict[str, Any], answer: dict[str, Any]) -> float:
    if answer.get("task_type") != dataset_row["task_family"]:
        return 0.0
    text = _answer_text(answer).lower()
    task = dataset_row["task_family"]
    if task == "unsafe_optimization_rejection":
        return WEIGHTS["task_alignment"] if ("reject" in text or "unsafe" in text or "risk" in text) else WEIGHTS["task_alignment"] * .5
    if task == "rtl_before_after_judgment":
        return WEIGHTS["task_alignment"] if ("before" in text or "after" in text or "compare" in text) else WEIGHTS["task_alignment"] * .5
    if task == "rtl_tool_report_explanation":
        return WEIGHTS["task_alignment"] if ("report" in text or "log" in text or "excerpt" in text) else WEIGHTS["task_alignment"] * .5
    return WEIGHTS["task_alignment"]


def evaluate_answer(dataset_row: dict[str, Any], candidate_answer: Any) -> RowEvalResult:
    errors: list[str] = []
    warnings: list[str] = []
    subscores = {name: 0.0 for name in WEIGHTS}
    subscores["schema_and_required_fields"] = _schema_score(dataset_row, candidate_answer, errors)
    validation_errors: list[str] = []
    if isinstance(candidate_answer, dict):
        validation_errors = _candidate_validation_errors(dataset_row, candidate_answer)
        errors.extend(validation_errors)
        subscores["issue_grounding"] = _issue_grounding_score(dataset_row, candidate_answer, warnings)
        subscores["reasoning_quality"] = _reasoning_score(candidate_answer)
        subscores["claim_safety"], safety_failures = _claim_safety_score(dataset_row, candidate_answer, validation_errors)
        subscores["verification_plan"] = _verification_plan_score(candidate_answer)
        subscores["task_alignment"] = _task_alignment_score(dataset_row, candidate_answer)
    else:
        safety_failures = []
    score = round(sum(subscores.values()), 6)
    if errors:
        score = min(score, 0.25)
    return RowEvalResult(str(dataset_row.get("id")), score, {key: round(value, 6) for key, value in subscores.items()}, errors, warnings, safety_failures)


def evaluate_dataset(dataset_rows: list[dict[str, Any]], candidates: dict[str, dict[str, Any]], candidate_rows: int, output_dir: Path, dataset_path: Path, candidates_path: Path, strict: bool = False) -> tuple[dict[str, Any], int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    row_results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    dataset_ids = {row["id"] for row in dataset_rows}
    candidate_ids = set(candidates)
    missing = sorted(dataset_ids - candidate_ids)
    extra = sorted(candidate_ids - dataset_ids)
    if missing:
        warnings.append(f"missing candidates: {', '.join(missing)}")
    unmatched = [{"id": row_id, "candidate": candidates[row_id]} for row_id in extra]
    for row in dataset_rows:
        if row["id"] not in candidates:
            continue
        result = evaluate_answer(row, candidates[row["id"]]["answer"])
        row_results.append(asdict(result))
    write_jsonl(output_dir / "row_results.jsonl", row_results)
    write_jsonl(output_dir / "unmatched_candidates.jsonl", unmatched)
    scores = [row["score"] for row in row_results]
    safety_failures = sum(len(row["safety_failures"]) for row in row_results)
    metrics = _metrics(dataset_rows, row_results, candidate_rows, len(missing), len(extra), safety_failures)
    (output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(_report(metrics, row_results, dataset_path, candidates_path), encoding="utf-8")
    if strict and (missing or extra):
        errors.append("strict mode fails on missing or extra candidates")
    if any(row["errors"] for row in row_results):
        errors.append("one or more candidate answers failed validation")
    summary = {
        "ok": not errors,
        "dataset_rows": len(dataset_rows),
        "candidate_rows": candidate_rows,
        "matched_rows": len(row_results),
        "missing_candidates": len(missing),
        "extra_candidates": len(extra),
        "mean_score": round(statistics.mean(scores), 6) if scores else 0.0,
        "safety_failures": safety_failures,
        "output_dir": str(output_dir),
        "errors": errors,
        "warnings": warnings,
    }
    return summary, 0 if not errors and (summary["ok"] or (row_results and not strict)) else 1


def _breakdown(dataset_rows: list[dict[str, Any]], row_results: list[dict[str, Any]], key: str) -> dict[str, float]:
    row_by_id = {row["id"]: row for row in dataset_rows}
    grouped: dict[str, list[float]] = defaultdict(list)
    for result in row_results:
        row = row_by_id[result["id"]]
        grouped[str(row.get(key))].append(result["score"])
    return {name: round(statistics.mean(scores), 6) for name, scores in sorted(grouped.items())}


def _metrics(dataset_rows: list[dict[str, Any]], row_results: list[dict[str, Any]], candidate_rows: int, missing: int, extra: int, safety_failures: int) -> dict[str, Any]:
    scores = [row["score"] for row in row_results]
    return {
        "dataset_rows": len(dataset_rows),
        "candidate_rows": candidate_rows,
        "matched_rows": len(row_results),
        "missing_candidates": missing,
        "extra_candidates": extra,
        "mean_score": round(statistics.mean(scores), 6) if scores else 0.0,
        "median_score": round(statistics.median(scores), 6) if scores else 0.0,
        "min_score": min(scores) if scores else 0.0,
        "max_score": max(scores) if scores else 0.0,
        "score_by_task_type": _breakdown(dataset_rows, row_results, "task_family"),
        "score_by_source": _breakdown(dataset_rows, row_results, "source"),
        "score_by_design_family": _breakdown(dataset_rows, row_results, "design_family"),
        "safety_failure_counts": dict(sorted(Counter(failure for row in row_results for failure in row["safety_failures"]).items())),
        "safety_failures": safety_failures,
        "error_counts": dict(sorted(Counter(error for row in row_results for error in row["errors"]).items())),
    }


def _report(metrics: dict[str, Any], row_results: list[dict[str, Any]], dataset_path: Path, candidates_path: Path) -> str:
    weakest = sorted(row_results, key=lambda row: row["score"])[:10]
    rows = "\n".join(f"| `{row['id']}` | {row['score']:.3f} | {len(row['errors'])} | {len(row['safety_failures'])} |" for row in weakest) or "| none | | | |"
    return f"""# Evaluation report

## Summary

- Dataset: `{dataset_path}`
- Candidates: `{candidates_path}`
- Matched rows: {metrics['matched_rows']}
- Mean score: {metrics['mean_score']}
- Safety failures: {metrics['safety_failures']}

## Rubric

- schema_and_required_fields: 0.20
- issue_grounding: 0.25
- reasoning_quality: 0.20
- claim_safety: 0.20
- verification_plan: 0.10
- task_alignment: 0.05

## Weakest rows

| Row | Score | Errors | Safety failures |
|---|---:|---:|---:|
{rows}

## Safety failure summary

```json
{json.dumps(metrics['safety_failure_counts'], indent=2)}
```

## Limitations

This deterministic harness checks structure, grounding signals, conservative reasoning, and claim safety. It is not semantic proof, does not execute RTL, and does not call an LLM.
"""


def load_dataset_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    report = validate_dataset_file(path, strict=True)
    errors = [item.format() for item in report.errors + report.warnings] if not report.ok else []
    loaded, problems = load_jsonl(path)
    errors.extend(problem.message for problem in problems)
    return [row for _, row in loaded], errors
