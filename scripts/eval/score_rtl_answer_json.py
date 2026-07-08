#!/usr/bin/env python3
"""Score model outputs against exported teacher-distill RTL evaluation prompts."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.claim_safety import find_unsupported_claims
from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.release import canonical_json
from scripts.dataset.rtl_answer_teacher_batches import (
    ANSWER_REQUIRED_FIELDS,
    _contains_task_copy,
    _has_candidate_source,
    _is_reference_only,
    _validate_answer_row,
)
from scripts.eval.model_candidate_runner import parse_model_output


REQUIRED_FIELD_TYPES = {
    "issue_summary": list,
    "verification_plan": list,
    "claim_levels": dict,
    "limitations": list,
}
CATEGORY_KEYS = (
    "json_valid",
    "schema_valid",
    "source_id_match",
    "required_fields_valid",
    "claim_safety_valid",
    "reference_only_behavior_valid",
    "candidate_bug_behavior_valid",
    "exact_expected_match_optional",
    "overall_valid",
)
COMPARE_CATEGORY_KEYS = (
    "overall_valid",
    "json_valid",
    "schema_valid",
    "claim_safety_valid",
    "source_id_match",
    "reference_only_behavior_valid",
    "candidate_bug_behavior_valid",
)


def _load_prompt_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    loaded, problems = load_jsonl(path)
    errors = [problem.message for problem in problems]
    rows = [row for _, row in loaded]
    for index, row in enumerate(rows, 1):
        for field in ("source_id", "split", "system_prompt", "user_prompt", "user_content", "expected_answer", "scoring_row"):
            if field not in row:
                errors.append(f"prompt row {index} is missing {field}")
        messages = row.get("prompt_messages")
        if not isinstance(messages, list) or len(messages) != 2:
            errors.append(f"prompt row {index} must contain two prompt_messages")
            continue
        roles = [message.get("role") if isinstance(message, dict) else None for message in messages]
        if roles != ["system", "user"]:
            errors.append(f"prompt row {index} prompt_messages must be system/user")
        user_content = row.get("user_content")
        expected_answer = row.get("expected_answer")
        if not isinstance(user_content, dict) or user_content.get("schema_version") != "rtl_task_v0.1":
            errors.append(f"prompt row {index} user_content must be rtl_task_v0.1")
        if not isinstance(expected_answer, dict) or expected_answer.get("schema_version") != "rtl_answer_v0.1":
            errors.append(f"prompt row {index} expected_answer must be rtl_answer_v0.1")
    return rows, errors


def _load_predictions(path: Path) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    loaded, problems = load_jsonl(path)
    errors = [problem.message for problem in problems]
    warnings: list[str] = []
    predictions: dict[str, dict[str, Any]] = {}
    for line, row in loaded:
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"line {line}: prediction source_id must be a non-empty string")
            continue
        if source_id in predictions:
            errors.append(f"line {line}: duplicate prediction source_id {source_id}")
            continue
        if not isinstance(row.get("model"), str) or not row["model"]:
            warnings.append(f"line {line}: prediction model is missing or empty")
        if "output" not in row:
            errors.append(f"line {line}: prediction output field is required")
            continue
        predictions[source_id] = row
    return predictions, errors, warnings


def _required_fields_valid(answer: Any) -> tuple[bool, list[str]]:
    if not isinstance(answer, dict):
        return False, ["parsed output is not an object"]
    issues: list[str] = []
    missing = sorted(ANSWER_REQUIRED_FIELDS - answer.keys())
    if missing:
        issues.append(f"missing required fields: {', '.join(missing)}")
    for field, expected_type in REQUIRED_FIELD_TYPES.items():
        value = answer.get(field)
        if field in answer and not isinstance(value, expected_type):
            issues.append(f"{field} must be a {expected_type.__name__}")
    return not issues, issues


def _schema_valid(prompt_row: dict[str, Any], answer: Any) -> tuple[bool, list[str]]:
    if not isinstance(answer, dict):
        return False, ["parsed output is not an object"]
    issues: list[str] = []
    if answer.get("schema_version") != "rtl_answer_v0.1":
        issues.append("schema_version must be rtl_answer_v0.1")
    task_type = prompt_row["metadata"].get("task_family")
    if answer.get("task_type") != task_type:
        issues.append("task_type must match prompt task_family")
    return not issues, issues


def _source_id_match(prompt_row: dict[str, Any], answer: Any) -> tuple[bool, list[str]]:
    if not isinstance(answer, dict):
        return False, ["parsed output is not an object"]
    if answer.get("source_id") != prompt_row.get("source_id"):
        return False, [f"answer source_id must match prompt source_id {prompt_row.get('source_id')}"]
    return True, []


def _candidate_discussion_present(answer: dict[str, Any]) -> bool:
    text = canonical_json(answer).lower()
    if any(token in text for token in ("candidate", "topmodule", "before_rtl_code", "prompt-embedded")):
        return True
    issues = answer.get("issue_summary")
    if not isinstance(issues, list):
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        evidence = issue.get("evidence")
        if isinstance(evidence, dict):
            location = evidence.get("code_location")
            if isinstance(location, dict) and location.get("module") == "TopModule":
                return True
            if evidence.get("reason") and "candidate" in str(evidence["reason"]).lower():
                return True
    return False


def _score_row(prompt_row: dict[str, Any], prediction: dict[str, Any] | None) -> dict[str, Any]:
    source_id = str(prompt_row["source_id"])
    task = deepcopy(prompt_row["user_content"])
    expected = prompt_row["expected_answer"]
    categories: dict[str, bool | None] = {key: False for key in CATEGORY_KEYS}
    errors: list[str] = []
    warnings: list[str] = []
    parse_status = "missing_prediction"
    model = prediction.get("model") if prediction else None
    parsed_answer: dict[str, Any] | None = None

    is_reference_only = _is_reference_only(task)
    has_candidate_source = _has_candidate_source(task)

    if prediction is None:
        errors.append("missing prediction")
        categories["reference_only_behavior_valid"] = False if is_reference_only else None
        categories["candidate_bug_behavior_valid"] = False if has_candidate_source else None
        return {
            "source_id": source_id,
            "model": model,
            "parse_status": parse_status,
            "categories": categories,
            "errors": errors,
            "warnings": warnings,
        }

    output = prediction.get("output")
    if isinstance(output, dict):
        parsed_answer = deepcopy(output)
        parse_status = "parsed_object"
        categories["json_valid"] = True
    elif isinstance(output, str):
        parse_result = parse_model_output(output)
        parse_status = parse_result.status
        if parse_result.answer is not None:
            parsed_answer = parse_result.answer
            categories["json_valid"] = True
        else:
            errors.append(parse_result.error or "could not parse output JSON")
    else:
        errors.append("prediction output must be a JSON object or raw JSON string")

    if parsed_answer is None:
        categories["reference_only_behavior_valid"] = False if is_reference_only else None
        categories["candidate_bug_behavior_valid"] = False if has_candidate_source else None
        return {
            "source_id": source_id,
            "model": model,
            "parse_status": parse_status,
            "categories": categories,
            "errors": errors,
            "warnings": warnings,
        }

    schema_valid, schema_issues = _schema_valid(prompt_row, parsed_answer)
    categories["schema_valid"] = schema_valid
    errors.extend(schema_issues)

    source_id_valid, source_id_issues = _source_id_match(prompt_row, parsed_answer)
    categories["source_id_match"] = source_id_valid
    errors.extend(source_id_issues)

    required_valid, required_issues = _required_fields_valid(parsed_answer)
    categories["required_fields_valid"] = required_valid
    errors.extend(required_issues)

    answer_validation_errors = _validate_answer_row(parsed_answer, task, 1)
    for message in answer_validation_errors:
        if message not in errors:
            errors.append(message)

    claim_issues = [message for _, message in find_unsupported_claims({
        "tool_checks": deepcopy(prompt_row["scoring_row"].get("tool_checks")),
        "messages": [
            {"role": "system", "content": prompt_row["system_prompt"]},
            {"role": "user", "content": deepcopy(task)},
            {"role": "assistant", "content": deepcopy(parsed_answer)},
        ],
    }, parsed_answer)]
    claim_issues.extend(
        message for message in answer_validation_errors
        if "unsupported" in message or "claim_levels" in message or "verified requires" in message
    )
    categories["claim_safety_valid"] = not claim_issues
    for message in claim_issues:
        if message not in errors:
            errors.append(message)

    if _contains_task_copy(parsed_answer):
        errors.append("assistant output appears to copy full rtl_task_v0.1 content")

    if is_reference_only:
        categories["reference_only_behavior_valid"] = not any(
            "reference-only task" in message or "candidate DUT bug" in message
            for message in answer_validation_errors
        )
        if categories["reference_only_behavior_valid"] is False:
            errors.append("reference-only row invented a candidate DUT bug")
    else:
        categories["reference_only_behavior_valid"] = None

    if has_candidate_source:
        categories["candidate_bug_behavior_valid"] = _candidate_discussion_present(parsed_answer)
        if categories["candidate_bug_behavior_valid"] is False:
            errors.append("candidate-bug row did not discuss the prompt-embedded candidate")
    else:
        categories["candidate_bug_behavior_valid"] = None

    categories["exact_expected_match_optional"] = canonical_json(parsed_answer) == canonical_json(expected)
    categories["overall_valid"] = all(
        (
            categories["json_valid"] is True,
            categories["schema_valid"] is True,
            categories["source_id_match"] is True,
            categories["required_fields_valid"] is True,
            categories["claim_safety_valid"] is True,
            categories["reference_only_behavior_valid"] in {True, None},
            categories["candidate_bug_behavior_valid"] in {True, None},
            not _contains_task_copy(parsed_answer),
        )
    )
    return {
        "source_id": source_id,
        "model": model,
        "parse_status": parse_status,
        "categories": categories,
        "errors": errors,
        "warnings": warnings,
    }


def _category_counts(row_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for key in CATEGORY_KEYS:
        relevant = [row["categories"][key] for row in row_results if row["categories"].get(key) is not None]
        passed = sum(value is True for value in relevant)
        failed = sum(value is False for value in relevant)
        total = len(relevant)
        counts[key] = {
            "passed": passed,
            "failed": failed,
            "total": total,
            "rate": round(passed / total, 6) if total else None,
        }
    return counts


def _failures_by_source(row_results: list[dict[str, Any]]) -> dict[str, list[str]]:
    failures: dict[str, list[str]] = {}
    for row in row_results:
        messages = list(dict.fromkeys(row["errors"] + row["warnings"]))
        if messages:
            failures[row["source_id"]] = messages
    return failures


def _score_markdown(summary: dict[str, Any]) -> str:
    rates = summary["category_counts"]
    weakest = [row for row in summary["row_results"] if not row["categories"]["overall_valid"]][:20]
    weakest_rows = "\n".join(
        f"| `{row['source_id']}` | {row['model'] or 'n/a'} | {row['parse_status']} | {', '.join(key for key, value in row['categories'].items() if value is False) or 'none'} |"
        for row in weakest
    ) or "| none | | | |"
    lines = [
        "# RTL answer scoring report",
        "",
        "## Summary",
        "",
        f"- Prompt rows: {summary['prompt_rows']}",
        f"- Prediction rows: {summary['prediction_rows']}",
        f"- Matched predictions: {summary['matched_predictions']}",
        f"- Missing predictions: {len(summary['missing_predictions'])}",
        f"- Extra predictions: {len(summary['extra_predictions'])}",
        f"- Models: {', '.join(summary['models']) if summary['models'] else 'none'}",
        "",
        "## Category rates",
        "",
        "| Category | Passed | Failed | Total | Rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in CATEGORY_KEYS:
        item = rates[key]
        rate_text = "n/a" if item["rate"] is None else f"{item['rate']:.3f}"
        lines.append(f"| `{key}` | {item['passed']} | {item['failed']} | {item['total']} | {rate_text} |")
    lines.extend([
        "",
        "## Failing rows",
        "",
        "| Source ID | Model | Parse status | Failed categories |",
        "|---|---|---|---|",
        weakest_rows,
        "",
        "## Limitations",
        "",
        "This scoring checks JSON shape, schema behavior, claim safety, reference-only behavior, and prompt-embedded candidate handling. It does not prove RTL correctness.",
        "",
    ])
    return "\n".join(lines)


def score_rtl_answer_json(
    prompts_path: Path,
    predictions_path: Path,
    output_json: Path,
    output_md: Path,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    prompts, prompt_errors = _load_prompt_rows(prompts_path)
    errors.extend(prompt_errors)
    predictions, prediction_errors, prediction_warnings = _load_predictions(predictions_path)
    errors.extend(prediction_errors)
    warnings.extend(prediction_warnings)
    if errors:
        summary = {
            "ok": False,
            "prompt_rows": len(prompts),
            "prediction_rows": len(predictions),
            "matched_predictions": 0,
            "missing_predictions": [],
            "extra_predictions": sorted(set(predictions) - {row.get("source_id") for row in prompts}),
            "models": sorted({str(row.get("model")) for row in predictions.values() if row.get("model")}),
            "category_counts": {},
            "row_results": [],
            "failures_by_source_id": {},
            "errors": errors,
            "warnings": warnings,
        }
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("# RTL answer scoring report\n\nScoring failed before row evaluation.\n", encoding="utf-8")
        return summary, 1

    prompt_by_source = {str(row["source_id"]): row for row in prompts}
    missing_predictions = sorted(set(prompt_by_source) - set(predictions))
    extra_predictions = sorted(set(predictions) - set(prompt_by_source))
    if missing_predictions:
        warnings.append(f"missing predictions: {', '.join(missing_predictions)}")
    if extra_predictions:
        warnings.append(f"extra predictions: {', '.join(extra_predictions)}")
    row_results = [_score_row(prompt_by_source[source_id], predictions.get(source_id)) for source_id in sorted(prompt_by_source)]
    if any(result["errors"] for result in row_results):
        warnings.append("one or more prediction rows failed structural or behavioral checks")
    category_counts = _category_counts(row_results)
    summary = {
        "ok": True,
        "prompt_rows": len(prompts),
        "prediction_rows": len(predictions),
        "matched_predictions": len(prompts) - len(missing_predictions),
        "missing_predictions": missing_predictions,
        "extra_predictions": extra_predictions,
        "models": sorted({str(row.get("model")) for row in predictions.values() if row.get("model")}),
        "category_counts": category_counts,
        "row_results": row_results,
        "failures_by_source_id": _failures_by_source(row_results),
        "errors": errors,
        "warnings": warnings,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(_score_markdown(summary), encoding="utf-8")
    exit_code = 0 if summary["ok"] and (not strict or not warnings) else 1
    return summary, exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = score_rtl_answer_json(
        prompts_path=args.prompts,
        predictions_path=args.predictions,
        output_json=args.output_json,
        output_md=args.output_md,
        strict=args.strict,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("RTL answer scoring completed." if result["ok"] else "RTL answer scoring failed.")
        print(f"Prompt rows: {result['prompt_rows']}")
        print(f"Prediction rows: {result['prediction_rows']}")
        print(f"Matched predictions: {result['matched_predictions']}")
        if result["errors"]:
            print("Errors:")
            for item in result["errors"]:
                print(f"- {item}")
        if result["warnings"]:
            print("Warnings:")
            for item in result["warnings"]:
                print(f"- {item}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
