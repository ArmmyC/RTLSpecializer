#!/usr/bin/env python3
"""Inspect semantic and structural differences between two candidate files."""

from __future__ import annotations

from copy import deepcopy
import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.eval.evaluator import load_candidate_answers, load_dataset_rows


_STOP_WORDS = {
    "a", "an", "and", "are", "before", "candidate", "change", "changed", "for", "from",
    "into", "is", "of", "on", "or", "rtl", "signal", "signals", "synthetic", "task",
    "the", "to", "type", "using", "with",
}


def _answer_text(answer: Any) -> str:
    return json.dumps(answer, ensure_ascii=False, sort_keys=True)


def _answer_text_without_source_id(answer: dict[str, Any]) -> str:
    scrubbed = deepcopy(answer)
    if isinstance(scrubbed, dict):
        scrubbed.pop("source_id", None)
    return _answer_text(scrubbed)


def _task(row: dict[str, Any]) -> dict[str, Any]:
    return row["messages"][1]["content"]


def _source_id(row: dict[str, Any]) -> str | None:
    task = _task(row)
    for value in (task.get("source_id"), row.get("source_id"), row.get("id")):
        if isinstance(value, str) and value:
            return value
    return None


def _issue_summary_text(answer: dict[str, Any]) -> str:
    issues = answer.get("issue_summary")
    if not isinstance(issues, list):
        return ""
    parts: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        text = issue.get("issue")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
        evidence = issue.get("evidence")
        reason = evidence.get("reason") if isinstance(evidence, dict) else None
        if isinstance(reason, str) and reason.strip():
            parts.append(reason.strip())
    return " ".join(parts)


def _signal_names(answer: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    issues = answer.get("issue_summary")
    if not isinstance(issues, list):
        return signals
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        evidence = issue.get("evidence")
        values = evidence.get("signal_names") if isinstance(evidence, dict) else None
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value not in signals:
                signals.append(value)
    return signals


def _patch_provided(answer: dict[str, Any]) -> bool | None:
    patch = answer.get("patch")
    if not isinstance(patch, dict):
        return None
    provided = patch.get("provided")
    return provided if isinstance(provided, bool) else None


def _mutation_terms(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    phrases: list[str] = []
    tokens: list[str] = []
    mutated_signals = {
        str(value).lower()
        for value in (_task(row).get("mutated_signal_names") or [])
        if isinstance(value, str) and value
    }
    source_id = _source_id(row)
    if source_id and "_synthetic_" in source_id:
        suffix = source_id.split("_synthetic_", 1)[1]
        phrase = suffix.replace("_", " ").strip().lower()
        if phrase:
            phrases.append(phrase)
    mutation_summary = _task(row).get("mutation_summary")
    if isinstance(mutation_summary, str) and mutation_summary.strip():
        phrases.append(mutation_summary.strip().lower())
    for phrase in phrases:
        for token in re.findall(r"[a-z0-9_]+", phrase):
            token = token.lower().strip("_")
            if (
                len(token) >= 4
                and token not in _STOP_WORDS
                and token not in mutated_signals
                and token not in tokens
            ):
                tokens.append(token)
    return phrases, tokens


def _mentions_mutation_type(row: dict[str, Any], answer: dict[str, Any]) -> bool:
    text = _answer_text_without_source_id(answer).lower()
    phrases, tokens = _mutation_terms(row)
    if any(phrase and phrase in text for phrase in phrases):
        return True
    present = [token for token in tokens if token in text]
    return len(present) >= min(2, len(tokens)) if tokens else False


def _mentions_mutated_signal_names(row: dict[str, Any], answer: dict[str, Any]) -> bool:
    values = _task(row).get("mutated_signal_names")
    if not isinstance(values, list):
        return False
    text = _answer_text(answer).lower()
    return any(isinstance(value, str) and value.lower() in text for value in values)


def _genericize_answer(answer: dict[str, Any]) -> dict[str, Any]:
    generic = deepcopy(answer)
    if isinstance(generic.get("source_id"), str):
        generic["source_id"] = "<source_id>"
    issues = generic.get("issue_summary")
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            evidence = issue.get("evidence")
            if isinstance(evidence, dict):
                if isinstance(evidence.get("signal_names"), list) and evidence["signal_names"]:
                    evidence["signal_names"] = ["<signal_names>"]
                location = evidence.get("code_location")
                if isinstance(location, dict):
                    if isinstance(location.get("module"), str):
                        location["module"] = "<module>"
                    if isinstance(location.get("block"), str):
                        location["block"] = "<block>"
    return generic


def analyze_duplicate_answers(candidates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    exact_groups: dict[str, list[str]] = {}
    generic_rows: list[tuple[str, str]] = []
    for row_id, item in candidates.items():
        answer = item["answer"]
        exact_key = _answer_text(answer)
        exact_groups.setdefault(exact_key, []).append(row_id)
        generic_rows.append((row_id, _answer_text(_genericize_answer(answer))))
    exact_duplicate_groups = [
        {"ids": sorted(ids), "count": len(ids)}
        for ids in exact_groups.values()
        if len(ids) > 1
    ]
    near_duplicate_pairs: list[dict[str, Any]] = []
    for index, (left_id, left_text) in enumerate(generic_rows):
        for right_id, right_text in generic_rows[index + 1:]:
            if left_text == right_text:
                near_duplicate_pairs.append({"ids": [left_id, right_id], "similarity": 1.0})
                continue
            similarity = SequenceMatcher(None, left_text, right_text).ratio()
            if similarity >= 0.95:
                near_duplicate_pairs.append({
                    "ids": [left_id, right_id],
                    "similarity": round(similarity, 6),
                })
    near_duplicate_pairs.sort(key=lambda item: (-item["similarity"], item["ids"]))
    return {
        "exact_duplicate_groups": sorted(exact_duplicate_groups, key=lambda item: (-item["count"], item["ids"])),
        "near_duplicate_pairs": near_duplicate_pairs,
    }


def _difference_score(
    *,
    issue_text_changed: bool,
    signal_names_changed: bool,
    claim_levels_changed: bool,
    evidence_used_changed: bool,
    limitations_changed: bool,
    patch_provided_changed: bool,
    mutation_mention_changed: bool,
    mutated_signal_mention_changed: bool,
) -> int:
    return sum(
        1
        for item in (
            issue_text_changed,
            signal_names_changed,
            claim_levels_changed,
            evidence_used_changed,
            limitations_changed,
            patch_provided_changed,
            mutation_mention_changed,
            mutated_signal_mention_changed,
        )
        if item
    )


def _row_difference(
    row: dict[str, Any],
    answer_a: dict[str, Any],
    answer_b: dict[str, Any],
    *,
    name_a: str,
    name_b: str,
) -> dict[str, Any]:
    issue_text_a = _issue_summary_text(answer_a)
    issue_text_b = _issue_summary_text(answer_b)
    signal_names_a = _signal_names(answer_a)
    signal_names_b = _signal_names(answer_b)
    claim_levels_a = answer_a.get("claim_levels") if isinstance(answer_a.get("claim_levels"), dict) else {}
    claim_levels_b = answer_b.get("claim_levels") if isinstance(answer_b.get("claim_levels"), dict) else {}
    evidence_used_a = answer_a.get("evidence_used") if isinstance(answer_a.get("evidence_used"), list) else []
    evidence_used_b = answer_b.get("evidence_used") if isinstance(answer_b.get("evidence_used"), list) else []
    limitations_a = answer_a.get("limitations") if isinstance(answer_a.get("limitations"), list) else []
    limitations_b = answer_b.get("limitations") if isinstance(answer_b.get("limitations"), list) else []
    patch_provided_a = _patch_provided(answer_a)
    patch_provided_b = _patch_provided(answer_b)
    mutation_mention_a = _mentions_mutation_type(row, answer_a)
    mutation_mention_b = _mentions_mutation_type(row, answer_b)
    mutated_signal_mention_a = _mentions_mutated_signal_names(row, answer_a)
    mutated_signal_mention_b = _mentions_mutated_signal_names(row, answer_b)
    exact_same = _answer_text(answer_a) == _answer_text(answer_b)
    near_duplicate = SequenceMatcher(
        None,
        _answer_text(_genericize_answer(answer_a)),
        _answer_text(_genericize_answer(answer_b)),
    ).ratio() >= 0.95
    score = _difference_score(
        issue_text_changed=issue_text_a != issue_text_b,
        signal_names_changed=signal_names_a != signal_names_b,
        claim_levels_changed=claim_levels_a != claim_levels_b,
        evidence_used_changed=evidence_used_a != evidence_used_b,
        limitations_changed=limitations_a != limitations_b,
        patch_provided_changed=patch_provided_a != patch_provided_b,
        mutation_mention_changed=mutation_mention_a != mutation_mention_b,
        mutated_signal_mention_changed=mutated_signal_mention_a != mutated_signal_mention_b,
    )
    return {
        "id": row["id"],
        "difference_score": score,
        "issue_summary_text": {name_a: issue_text_a, name_b: issue_text_b},
        "signal_names": {name_a: signal_names_a, name_b: signal_names_b},
        "claim_levels": {name_a: claim_levels_a, name_b: claim_levels_b},
        "evidence_used": {name_a: evidence_used_a, name_b: evidence_used_b},
        "limitations": {name_a: limitations_a, name_b: limitations_b},
        "patch_provided": {name_a: patch_provided_a, name_b: patch_provided_b},
        "mentions_mutation_type": {name_a: mutation_mention_a, name_b: mutation_mention_b},
        "mentions_mutated_signal_names": {name_a: mutated_signal_mention_a, name_b: mutated_signal_mention_b},
        "exact_same_answer": exact_same,
        "near_duplicate_answer": near_duplicate,
    }


def _markdown(summary: dict[str, Any]) -> str:
    top_rows = summary["top_differences"]
    duplicate_a = summary["duplicate_analysis"][summary["name_a"]]
    duplicate_b = summary["duplicate_analysis"][summary["name_b"]]
    lines = [
        "# Candidate difference inspection",
        "",
        "## Summary",
        "",
        f"- Dataset: `{summary['dataset']}`",
        f"- Shared rows: {summary['shared_rows']}",
        f"- Missing in `{summary['name_a']}`: {len(summary['missing_in_a'])}",
        f"- Missing in `{summary['name_b']}`: {len(summary['missing_in_b'])}",
        f"- Extra in `{summary['name_a']}`: {len(summary['extra_in_a'])}",
        f"- Extra in `{summary['name_b']}`: {len(summary['extra_in_b'])}",
        "",
        "## Duplicate detection",
        "",
        f"- `{summary['name_a']}` exact duplicate groups: {len(duplicate_a['exact_duplicate_groups'])}",
        f"- `{summary['name_a']}` near-duplicate pairs: {len(duplicate_a['near_duplicate_pairs'])}",
        f"- `{summary['name_b']}` exact duplicate groups: {len(duplicate_b['exact_duplicate_groups'])}",
        f"- `{summary['name_b']}` near-duplicate pairs: {len(duplicate_b['near_duplicate_pairs'])}",
        "",
        "## Top differing rows",
        "",
    ]
    if not top_rows:
        lines.append("No shared rows differed.")
    else:
        lines.append("| Row | Difference score | Mutation mention | Mutated signal mention |")
        lines.append("|---|---:|---|---|")
        for item in top_rows:
            mutation_text = (
                f"{summary['name_a']}={item['mentions_mutation_type'][summary['name_a']]}, "
                f"{summary['name_b']}={item['mentions_mutation_type'][summary['name_b']]}"
            )
            signal_text = (
                f"{summary['name_a']}={item['mentions_mutated_signal_names'][summary['name_a']]}, "
                f"{summary['name_b']}={item['mentions_mutated_signal_names'][summary['name_b']]}"
            )
            lines.append(f"| `{item['id']}` | {item['difference_score']} | {mutation_text} | {signal_text} |")
    return "\n".join(lines).rstrip() + "\n"


def inspect_candidate_differences(
    *,
    dataset: Path,
    candidates_a: Path,
    name_a: str,
    candidates_b: Path,
    name_b: str,
    output_md: Path,
    output_json: Path,
) -> tuple[dict[str, Any], int]:
    dataset_rows, dataset_errors = load_dataset_rows(dataset)
    candidate_result_a = load_candidate_answers(candidates_a)
    candidate_result_b = load_candidate_answers(candidates_b)
    errors = dataset_errors + candidate_result_a.errors + candidate_result_b.errors
    warnings: list[str] = []
    if errors:
        summary = {"ok": False, "errors": errors, "warnings": warnings}
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text("# Candidate difference inspection\n\nInspection failed.\n", encoding="utf-8")
        return summary, 1

    dataset_map = {row["id"]: row for row in dataset_rows}
    ids_a = set(candidate_result_a.candidates)
    ids_b = set(candidate_result_b.candidates)
    shared_ids = sorted(ids_a & ids_b & set(dataset_map))
    missing_in_a = sorted((ids_b & set(dataset_map)) - ids_a)
    missing_in_b = sorted((ids_a & set(dataset_map)) - ids_b)
    extra_in_a = sorted(ids_a - set(dataset_map))
    extra_in_b = sorted(ids_b - set(dataset_map))
    if extra_in_a:
        warnings.append(f"{name_a} has candidate IDs not present in dataset")
    if extra_in_b:
        warnings.append(f"{name_b} has candidate IDs not present in dataset")

    differences = [
        _row_difference(
            dataset_map[row_id],
            candidate_result_a.candidates[row_id]["answer"],
            candidate_result_b.candidates[row_id]["answer"],
            name_a=name_a,
            name_b=name_b,
        )
        for row_id in shared_ids
    ]
    differences.sort(key=lambda item: (-item["difference_score"], item["id"]))
    summary = {
        "ok": True,
        "dataset": str(dataset),
        "name_a": name_a,
        "name_b": name_b,
        "candidates_a": str(candidates_a),
        "candidates_b": str(candidates_b),
        "shared_rows": len(shared_ids),
        "missing_in_a": missing_in_a,
        "missing_in_b": missing_in_b,
        "extra_in_a": extra_in_a,
        "extra_in_b": extra_in_b,
        "top_differences": differences[:20],
        "row_differences": differences,
        "duplicate_analysis": {
            name_a: analyze_duplicate_answers(candidate_result_a.candidates),
            name_b: analyze_duplicate_answers(candidate_result_b.candidates),
        },
        "errors": [],
        "warnings": warnings,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(_markdown(summary), encoding="utf-8")
    return summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--candidates-a", required=True, type=Path)
    parser.add_argument("--name-a", required=True)
    parser.add_argument("--candidates-b", required=True, type=Path)
    parser.add_argument("--name-b", required=True)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, code = inspect_candidate_differences(
        dataset=args.dataset,
        candidates_a=args.candidates_a,
        name_a=args.name_a,
        candidates_b=args.candidates_b,
        name_b=args.name_b,
        output_md=args.output_md,
        output_json=args.output_json,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Candidate difference inspection completed." if result["ok"] else "Candidate difference inspection failed.")
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
