"""Teacher-distilled pilot dataset preparation for clean rtl_task/rtl_answer pairs."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import random
from pathlib import Path
from typing import Any

from scripts.dataset.claim_safety import find_unsupported_claims
from scripts.dataset.constants import (
    ANSWER_SCHEMA_VERSION,
    ANSWER_SCHEMA_VERSIONS,
    DATASET_VERSION,
    TASK_SCHEMA_VERSION,
    TASK_SCHEMA_VERSIONS,
    TEACHER_DISTILL_REVIEW_STATUS,
    TOOL_CHECKS,
)
from scripts.dataset.io_utils import write_jsonl
from scripts.dataset.release import file_sha256
from scripts.dataset.rtl_answer_teacher_batches import (
    DEFAULT_SYSTEM_PROMPT,
    _contains_task_copy,
    _has_candidate_source,
    _load_answer_rows,
    _load_task_rows,
    _task_by_source,
    _validate_answer_row,
)
from scripts.dataset.validation import validate_dataset_file


DATASET_NAME = "verilog_eval_teacher_distill_v0_1"
DATASET_SEMVER = "v0.1"
DATASET_STAGE = "teacher_distill_pilot"
APPROVAL_STATUS = "not_approved"
SOURCE_FAMILY = "public_verilog_eval"
SOURCE_ENUM = "teacher_generated"
SCHEMA_PAIR = f"{TASK_SCHEMA_VERSION}_to_{ANSWER_SCHEMA_VERSION}"
CREATED_BY = "prepare_teacher_distill_dataset"
SPLIT_NAMES = ("train", "validation", "test")
KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS = (
    "Prob062_bugs_mux2",
    "Prob123_bugs_addsubz",
    "Prob132_always_if2",
)
MANIFEST_SELF_HASH_NOTE = (
    "A manifest cannot embed a SHA256 of its own final bytes without changing "
    "those bytes, so manifest.json is excluded from the output hash table."
)


def _contains_answer_copy(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("schema_version") in ANSWER_SCHEMA_VERSIONS:
            return True
        answer_only_keys = {
            "issue_summary",
            "time_reasoning",
            "space_reasoning",
            "safe_optimization",
            "functional_risk",
            "verification_plan",
            "claim_levels",
        }
        if len(answer_only_keys & value.keys()) >= 3:
            return True
        return any(_contains_answer_copy(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_answer_copy(item) for item in value)
    return False


def _tool_checks_template() -> dict[str, None]:
    return {name: None for name in sorted(TOOL_CHECKS)}


def _is_golden_path(path: Path) -> bool:
    return any(part.lower() == "golden" for part in path.parts)


def _output_file_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "all": output_dir / "all.jsonl",
        "train": output_dir / "train.jsonl",
        "validation": output_dir / "validation.jsonl",
        "test": output_dir / "test.jsonl",
        "manifest": output_dir / "manifest.json",
        "dataset_card": output_dir / "dataset_card.md",
        "validation_report_json": output_dir / "validation_report.json",
        "validation_report_md": output_dir / "validation_report.md",
    }


def _validate_output_dir(output_dir: Path) -> list[str]:
    errors: list[str] = []
    if _is_golden_path(output_dir):
        errors.append("--output-dir must not write into data/golden")
    if output_dir.exists() and output_dir.is_symlink():
        errors.append(f"--output-dir must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        errors.append(f"--output-dir exists and is not a directory: {output_dir}")
    for path in _output_file_paths(output_dir).values():
        if path.exists() and path.is_symlink():
            errors.append(f"refusing to overwrite symlinked output file: {path}")
    return errors


def _normalize_provenance(task: dict[str, Any]) -> dict[str, Any]:
    provenance = task.get("provenance") if isinstance(task.get("provenance"), dict) else {}
    original_notes = provenance.get("notes")
    notes = [
        "Teacher-distilled pilot row assembled from clean rtl_task_v0.1 and rtl_answer_v0.1 JSON.",
        "Not human-reviewed. Not golden. Not approved. Confirm license/provenance before any broader release or promotion.",
    ]
    if isinstance(original_notes, str) and original_notes.strip():
        notes.append(f"Original task provenance note: {original_notes.strip()}")
    public_dataset_name = provenance.get("public_dataset_name")
    if not isinstance(public_dataset_name, str) or not public_dataset_name.strip():
        public_dataset_name = task.get("source_dataset")
    public_dataset_url = provenance.get("public_dataset_url")
    if not isinstance(public_dataset_url, str) or not public_dataset_url.strip():
        public_dataset_url = None
    source_commit = provenance.get("source_commit")
    if not isinstance(source_commit, str) or not source_commit.strip():
        source_commit = None
    return {
        "origin": DATASET_STAGE,
        "public_dataset_name": public_dataset_name if isinstance(public_dataset_name, str) and public_dataset_name else None,
        "public_dataset_url": public_dataset_url,
        "source_commit": source_commit,
        "notes": " ".join(notes),
    }


def _top_level_tool_checks(task: dict[str, Any]) -> dict[str, Any]:
    checks = task.get("tool_checks")
    if isinstance(checks, dict):
        return deepcopy(checks)
    return _tool_checks_template()


def _derive_source_family(tasks: list[dict[str, Any]]) -> str:
    origins = {
        provenance.get("origin")
        for task in tasks
        if isinstance(task, dict)
        for provenance in [task.get("provenance")]
        if isinstance(provenance, dict) and isinstance(provenance.get("origin"), str) and provenance.get("origin")
    }
    source_datasets = {
        task.get("source_dataset")
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("source_dataset"), str) and task.get("source_dataset")
    }
    if "external_rtlcoder_gpt_generated_unverified" in origins:
        return "external_rtlcoder_gpt_generated_unverified"
    if len(origins) == 1:
        return next(iter(origins))
    if len(source_datasets) == 1:
        return next(iter(source_datasets))
    if "public_verilog_eval" in source_datasets:
        return "public_verilog_eval"
    return SOURCE_FAMILY


def _derive_dataset_name(tasks: list[dict[str, Any]], source_family: str) -> str:
    has_synthetic_bug = any(task.get("synthetic_bug") is True for task in tasks if isinstance(task, dict))
    source_datasets = {
        task.get("source_dataset")
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("source_dataset"), str) and task.get("source_dataset")
    }
    if source_family == "external_rtlcoder_gpt_generated_unverified" or any(
        isinstance(name, str) and name.lower().startswith("rtlcoder") for name in source_datasets
    ):
        return "rtlcoder_synthetic_teacher_distill_v0_1" if has_synthetic_bug else "rtlcoder_teacher_distill_v0_1"
    if source_family == "public_verilog_eval":
        return DATASET_NAME
    slug = "".join(char if char.isalnum() else "_" for char in source_family.lower()).strip("_")
    slug = slug or "teacher_distill"
    return f"{slug}_teacher_distill_v0_1"


def _make_row(
    task: dict[str, Any],
    answer: dict[str, Any],
    split: str,
    seed: int,
    *,
    dataset_name: str,
    source_family: str,
) -> dict[str, Any]:
    source_id = str(task.get("source_id"))
    return {
        "id": f"teacher_distill_{source_id}",
        "source_id": source_id,
        "dataset_name": dataset_name,
        "dataset_version": DATASET_VERSION,
        "dataset_stage": DATASET_STAGE,
        "schema_pair": SCHEMA_PAIR,
        "split": split,
        "split_seed": seed,
        "source": SOURCE_ENUM,
        "source_family": source_family,
        "license": task.get("license"),
        "design_family": task.get("design_family"),
        "task_family": task.get("task_type"),
        "created_by": CREATED_BY,
        "review_status": TEACHER_DISTILL_REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "promotion_allowed": False,
        "provenance": _normalize_provenance(task),
        "tool_checks": _top_level_tool_checks(task),
        "messages": [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": deepcopy(task)},
            {"role": "assistant", "content": deepcopy(answer)},
        ],
    }


def _split_assignments(
    task_by_source: dict[str, dict[str, Any]],
    train_size: int,
    validation_size: int,
    test_size: int,
    seed: int,
) -> dict[str, list[str]]:
    counts = {"train": train_size, "validation": validation_size, "test": test_size}
    candidate_ids = [source_id for source_id in sorted(task_by_source) if _has_candidate_source(task_by_source[source_id])]
    candidate_set = set(candidate_ids)
    rng = random.Random(seed)
    rng.shuffle(candidate_ids)
    assignments = {name: [] for name in SPLIT_NAMES}
    preferred_cycle = ("train", "test", "validation")

    for index, source_id in enumerate(candidate_ids):
        ordered_preferences = preferred_cycle[index % len(preferred_cycle):] + preferred_cycle[: index % len(preferred_cycle)]
        split_name = next((name for name in ordered_preferences if counts[name] > 0), None)
        if split_name is None:
            raise ValueError("no split capacity remains for prompt-embedded candidate rows")
        assignments[split_name].append(source_id)
        counts[split_name] -= 1

    remaining_ids = [source_id for source_id in sorted(task_by_source) if source_id not in candidate_set]
    rng.shuffle(remaining_ids)
    offset = 0
    for split_name in SPLIT_NAMES:
        take = counts[split_name]
        assignments[split_name].extend(remaining_ids[offset:offset + take])
        offset += take
    if offset != len(remaining_ids):
        raise ValueError("split assignment did not consume every remaining source_id")
    return {name: sorted(values) for name, values in assignments.items()}


def _split_rows(rows_by_source: dict[str, dict[str, Any]], assignments: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    return {
        split_name: [rows_by_source[source_id] for source_id in assignments[split_name]]
        for split_name in SPLIT_NAMES
    }


def _source_id_range(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = sorted(str(row.get("source_id")) for row in rows)
    return {
        "count": len(source_ids),
        "first": source_ids[0] if source_ids else None,
        "last": source_ids[-1] if source_ids else None,
    }


def _candidate_row_assignments(assignments: dict[str, list[str]]) -> list[dict[str, Any]]:
    split_by_source = {
        source_id: split_name
        for split_name, source_ids in assignments.items()
        for source_id in source_ids
    }
    return [
        {
            "source_id": source_id,
            "present": source_id in split_by_source,
            "assigned_split": split_by_source.get(source_id),
        }
        for source_id in KNOWN_PROMPT_EMBEDDED_CANDIDATE_ROWS
    ]


def _preflight_validation(
    tasks_path: Path,
    answers_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    tasks, task_errors = _load_task_rows(tasks_path)
    answers, answer_errors, _ = _load_answer_rows(answers_path)
    errors.extend(task_errors)
    errors.extend(answer_errors)
    task_by_source, task_source_errors = _task_by_source(tasks)
    errors.extend(task_source_errors)

    answer_by_source: dict[str, dict[str, Any]] = {}
    duplicate_answer_ids: list[str] = []
    validation_failures = 0
    for index, answer in enumerate(answers, 1):
        source_id = answer.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"answer row {index} is missing source_id")
            validation_failures += 1
            continue
        if source_id in answer_by_source:
            duplicate_answer_ids.append(source_id)
            errors.append(f"answer row {index} duplicates source_id {source_id}")
            validation_failures += 1
            continue
        answer_by_source[source_id] = answer
        task = task_by_source.get(source_id)
        if task is None:
            errors.append(f"answer row {index} has unknown source_id {source_id}")
            validation_failures += 1
            continue
        for detail in _validate_answer_row(answer, task, index):
            errors.append(detail)
            validation_failures += 1

    missing_answer_ids = sorted(set(task_by_source) - set(answer_by_source))
    extra_answer_ids = sorted(set(answer_by_source) - set(task_by_source))
    duplicate_task_ids = sorted({item.split(": ", 1)[1] for item in task_source_errors if "duplicate source_id" in item})
    if missing_answer_ids:
        errors.append(f"tasks missing answers for source_id values: {', '.join(missing_answer_ids)}")
    if extra_answer_ids:
        errors.append(f"answers contain source_id values without tasks: {', '.join(extra_answer_ids)}")

    detail = {
        "task_rows": len(tasks),
        "answer_rows": len(answers),
        "matched_rows": len(set(task_by_source) & set(answer_by_source)),
        "missing_answer_source_ids": missing_answer_ids,
        "extra_answer_source_ids": extra_answer_ids,
        "duplicate_task_source_ids": duplicate_task_ids,
        "duplicate_answer_source_ids": sorted(set(duplicate_answer_ids)),
        "answer_validation_failures": validation_failures,
    }
    return tasks, answers, detail, errors, warnings


def _row_checks(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    duplicate_source_ids: list[str] = []
    seen_source_ids: set[str] = set()
    role_order_failures = 0
    user_schema_failures = 0
    assistant_schema_failures = 0
    user_leakage_failures = 0
    assistant_leakage_failures = 0
    claim_wording_failures = 0
    evidence_failures = 0
    golden_or_approved_failures = 0

    for row in rows:
        source_id = str(row.get("source_id"))
        if source_id in seen_source_ids:
            duplicate_source_ids.append(source_id)
        seen_source_ids.add(source_id)
        messages = row.get("messages")
        if not isinstance(messages, list) or [item.get("role") for item in messages if isinstance(item, dict)] != ["system", "user", "assistant"]:
            role_order_failures += 1
            errors.append(f"row {source_id} does not preserve system/user/assistant role order")
            continue
        task = messages[1].get("content")
        answer = messages[2].get("content")
        if not isinstance(task, dict) or task.get("schema_version") not in TASK_SCHEMA_VERSIONS:
            user_schema_failures += 1
            errors.append(f"row {source_id} user content must contain {TASK_SCHEMA_VERSION}")
        if not isinstance(answer, dict) or answer.get("schema_version") not in ANSWER_SCHEMA_VERSIONS:
            assistant_schema_failures += 1
            errors.append(f"row {source_id} assistant content must contain {ANSWER_SCHEMA_VERSION}")
        if _contains_answer_copy(task):
            user_leakage_failures += 1
            errors.append(f"row {source_id} user content contains rtl_answer_v0.1 content")
        if _contains_task_copy(answer):
            assistant_leakage_failures += 1
            errors.append(f"row {source_id} assistant content contains rtl_task_v0.1 content")
        answer_errors = _validate_answer_row(answer, task, 1)
        evidence_failures += len(answer_errors)
        errors.extend(f"row {source_id} {detail}" for detail in answer_errors)
        claim_issues = find_unsupported_claims(row, answer)
        claim_wording_failures += len(claim_issues)
        errors.extend(f"row {source_id} {message}" for _, message in claim_issues)
        if row.get("review_status") != TEACHER_DISTILL_REVIEW_STATUS or row.get("approval_status") != APPROVAL_STATUS:
            golden_or_approved_failures += 1
            errors.append(f"row {source_id} must remain teacher_distilled_unreviewed and not_approved")
        if row.get("source") == "handwritten_golden" or str(row.get("id", "")).startswith("golden_"):
            golden_or_approved_failures += 1
            errors.append(f"row {source_id} must not be marked or named as golden")

    detail = {
        "duplicate_source_ids": sorted(set(duplicate_source_ids)),
        "role_order_checks": {"passed": len(rows) - role_order_failures, "failed": role_order_failures},
        "schema_checks": {
            "user_task_schema_passed": len(rows) - user_schema_failures,
            "user_task_schema_failed": user_schema_failures,
            "assistant_answer_schema_passed": len(rows) - assistant_schema_failures,
            "assistant_answer_schema_failed": assistant_schema_failures,
        },
        "leakage_checks": {
            "user_contains_answer_content_failures": user_leakage_failures,
            "assistant_contains_task_content_failures": assistant_leakage_failures,
        },
        "claim_wording_checks": {"failed": claim_wording_failures},
        "evidence_checks": {"failed": evidence_failures},
        "golden_approval_checks": {"failed": golden_or_approved_failures},
    }
    return detail, errors


def _validation_report(
    tasks_path: Path,
    answers_path: Path,
    rows: list[dict[str, Any]],
    split_rows: dict[str, list[dict[str, Any]]],
    preflight: dict[str, Any],
    row_checks: dict[str, Any],
    dataset_validator: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": not errors and not warnings,
        "tasks": str(tasks_path),
        "answers": str(answers_path),
        "row_count": len(rows),
        "split_counts": {name: len(items) for name, items in split_rows.items()},
        "source_pair_checks": preflight,
        "schema_checks": row_checks["schema_checks"],
        "claim_wording_checks": row_checks["claim_wording_checks"],
        "evidence_checks": row_checks["evidence_checks"],
        "role_order_checks": row_checks["role_order_checks"],
        "leakage_checks": row_checks["leakage_checks"],
        "golden_approval_checks": row_checks["golden_approval_checks"],
        "duplicate_source_ids": row_checks["duplicate_source_ids"],
        "dataset_validator": dataset_validator,
        "errors": errors,
        "warnings": warnings,
        "final_pass": not errors and not warnings,
    }


def _validation_markdown(report: dict[str, Any]) -> str:
    split_counts = report["split_counts"]
    pair = report["source_pair_checks"]
    validator = report["dataset_validator"]
    lines = [
        "# Teacher Distill Validation Report",
        "",
        f"- OK: {str(report['ok']).lower()}",
        f"- Row count: {report['row_count']}",
        f"- Train: {split_counts['train']}",
        f"- Validation: {split_counts['validation']}",
        f"- Test: {split_counts['test']}",
        f"- Missing answer IDs: {len(pair['missing_answer_source_ids'])}",
        f"- Extra answer IDs: {len(pair['extra_answer_source_ids'])}",
        f"- Duplicate task IDs: {len(pair['duplicate_task_source_ids'])}",
        f"- Duplicate answer IDs: {len(pair['duplicate_answer_source_ids'])}",
        f"- Dataset validator OK: {str(validator['ok']).lower()}",
        "",
        "## Checks",
        "",
        f"- Role order failures: {report['role_order_checks']['failed']}",
        f"- User leakage failures: {report['leakage_checks']['user_contains_answer_content_failures']}",
        f"- Assistant leakage failures: {report['leakage_checks']['assistant_contains_task_content_failures']}",
        f"- Claim wording failures: {report['claim_wording_checks']['failed']}",
        f"- Evidence failures: {report['evidence_checks']['failed']}",
        f"- Golden/approval failures: {report['golden_approval_checks']['failed']}",
        "",
        "## Errors",
        "",
    ]
    if report["errors"]:
        lines.extend(f"- {item}" for item in report["errors"])
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {item}" for item in report["warnings"])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _dataset_card(dataset_name: str, split_rows: dict[str, list[dict[str, Any]]], seed: int) -> str:
    candidate_count = sum(
        1 for rows in split_rows.values() for row in rows
        if _has_candidate_source(row["messages"][1]["content"])
    )
    return f"""# Dataset card: {dataset_name}

## Status

This is a teacher-distilled pilot dataset.
It is not human-reviewed.
It is not golden.
It is intended for format/pipeline/pilot fine-tuning only.
It should not be used as final production truth without review.

## Composition

- Train rows: {len(split_rows['train'])}
- Validation rows: {len(split_rows['validation'])}
- Test rows: {len(split_rows['test'])}
- Split seed: {seed}

## Evidence and claim policy

Tool evidence is mostly absent/null.
Claims are conservative text-inspection answers.
No row should be read as verified simulation, lint, synthesis, timing, toggle, area, or power truth without corresponding evidence.

## Provenance and release warning

License/provenance must be confirmed before any broader release or promotion.
These rows remain teacher-distilled and unreviewed, with approval_status set to not_approved.

## Main known limitation

Most rows are reference-only, with only a small number of prompt-embedded candidate bug rows ({candidate_count} detected in this pilot split set).

## Intended use

Use this dataset for pilot format checks, baseline-vs-fine-tune pipeline smoke tests, and a small LoRA/QLoRA experiment only.
"""


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _manifest(
    tasks_path: Path,
    answers_path: Path,
    output_dir: Path,
    tasks: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    split_rows: dict[str, list[dict[str, Any]]],
    seed: int,
    output_hashes: dict[str, dict[str, Any]],
    dataset_name: str,
) -> dict[str, Any]:
    all_rows = split_rows["train"] + split_rows["validation"] + split_rows["test"]
    return {
        "dataset_name": dataset_name,
        "dataset_version": DATASET_SEMVER,
        "created_by_script": "scripts/dataset/prepare_teacher_distill_dataset.py",
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "input_files": {
            "tasks": {"path": str(tasks_path), "sha256": file_sha256(tasks_path), "rows": len(tasks)},
            "answers": {"path": str(answers_path), "sha256": file_sha256(answers_path), "rows": len(answers)},
        },
        "output_files": output_hashes,
        "row_counts": {"all": len(all_rows), "tasks": len(tasks), "answers": len(answers)},
        "split_counts": {name: len(rows) for name, rows in split_rows.items()},
        "source_id_ranges": {
            "all": _source_id_range(all_rows),
            "train": _source_id_range(split_rows["train"]),
            "validation": _source_id_range(split_rows["validation"]),
            "test": _source_id_range(split_rows["test"]),
        },
        "prompt_embedded_candidate_bug_rows": _candidate_row_assignments({
            name: [str(row.get("source_id")) for row in rows]
            for name, rows in split_rows.items()
        }),
        "split_assignment": {
            "seed": seed,
            "strategy": "Round-robin prompt-embedded candidate rows across train/test/validation, then seeded shuffle of remaining source_ids.",
        },
        "review_status": TEACHER_DISTILL_REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "promotion_allowed": False,
        "notes": [
            "Teacher-distilled pilot dataset for local fine-tuning experiments only.",
            "Not human-reviewed. Not golden. Not approved.",
            "Most rows are reference-only and should be interpreted as conservative text-inspection answers.",
            "Confirm upstream provenance and license before any broader release or promotion.",
            MANIFEST_SELF_HASH_NOTE,
        ],
    }


def prepare_teacher_distill_dataset(
    tasks_path: Path,
    answers_path: Path,
    output_dir: Path,
    train_size: int,
    validation_size: int,
    test_size: int,
    seed: int = 42,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    total_requested = train_size + validation_size + test_size
    if min(train_size, validation_size, test_size) < 0:
        errors.append("split sizes must be non-negative")
    errors.extend(_validate_output_dir(output_dir))
    tasks, answers, preflight, preflight_errors, preflight_warnings = _preflight_validation(tasks_path, answers_path)
    errors.extend(preflight_errors)
    warnings.extend(preflight_warnings)
    if total_requested != len(tasks):
        errors.append(f"split sizes must sum to task row count: requested {total_requested}, task rows {len(tasks)}")
    if total_requested != len(answers):
        errors.append(f"split sizes must sum to answer row count: requested {total_requested}, answer rows {len(answers)}")
    task_by_source, task_source_errors = _task_by_source(tasks)
    errors.extend(task_source_errors)
    answer_by_source = {str(answer.get("source_id")): answer for answer in answers if isinstance(answer.get("source_id"), str)}
    source_family = _derive_source_family(tasks)
    dataset_name = _derive_dataset_name(tasks, source_family)
    if errors:
        return _result(False, dataset_name, output_dir, {}, {}, errors, warnings), 1

    try:
        assignments = _split_assignments(task_by_source, train_size, validation_size, test_size, seed)
    except ValueError as exc:
        return _result(False, dataset_name, output_dir, {}, {}, [str(exc)], warnings), 1

    rows_by_source = {
        source_id: _make_row(
            task,
            answer_by_source[source_id],
            split_name,
            seed,
            dataset_name=dataset_name,
            source_family=source_family,
        )
        for split_name, source_ids in assignments.items()
        for source_id in source_ids
        for task in [task_by_source[source_id]]
    }
    split_rows = _split_rows(rows_by_source, assignments)
    all_rows = split_rows["train"] + split_rows["validation"] + split_rows["test"]
    row_checks, row_errors = _row_checks(all_rows)
    errors.extend(row_errors)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = _output_file_paths(output_dir)
    write_jsonl(output_paths["all"], all_rows)
    for split_name in SPLIT_NAMES:
        write_jsonl(output_paths[split_name], split_rows[split_name])

    dataset_validator_report = validate_dataset_file(output_paths["all"], strict=True)
    dataset_validator = {
        "ok": dataset_validator_report.ok,
        "errors": [item.format() for item in dataset_validator_report.errors],
        "warnings": [item.format() for item in dataset_validator_report.warnings],
    }
    errors.extend(dataset_validator["errors"])
    if strict:
        errors.extend(dataset_validator["warnings"])
    else:
        warnings.extend(dataset_validator["warnings"])

    report = _validation_report(
        tasks_path=tasks_path,
        answers_path=answers_path,
        rows=all_rows,
        split_rows=split_rows,
        preflight=preflight,
        row_checks=row_checks,
        dataset_validator=dataset_validator,
        errors=errors,
        warnings=warnings,
    )
    _write_json(output_paths["validation_report_json"], report)
    output_paths["validation_report_md"].write_text(_validation_markdown(report), encoding="utf-8")
    output_paths["dataset_card"].write_text(_dataset_card(dataset_name, split_rows, seed), encoding="utf-8")

    output_hashes = {
        key: {"path": str(path), "sha256": file_sha256(path), "rows": len(split_rows[key]) if key in split_rows else 1}
        for key, path in output_paths.items()
        if key != "manifest"
    }
    manifest = _manifest(tasks_path, answers_path, output_dir, tasks, answers, split_rows, seed, output_hashes, dataset_name)
    _write_json(output_paths["manifest"], manifest)

    ok = not errors and (not strict or not warnings)
    result = _result(
        ok,
        dataset_name,
        output_dir,
        {name: len(rows) for name, rows in split_rows.items()},
        {key: str(path) for key, path in output_paths.items()},
        errors,
        warnings,
    )
    result["candidate_bug_rows"] = manifest["prompt_embedded_candidate_bug_rows"]
    return result, 0 if ok else 1


def _result(
    ok: bool,
    dataset_name: str,
    output_dir: Path,
    split_counts: dict[str, int],
    output_paths: dict[str, str],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "dataset_name": dataset_name,
        "dataset_stage": DATASET_STAGE,
        "review_status": TEACHER_DISTILL_REVIEW_STATUS,
        "approval_status": APPROVAL_STATUS,
        "output_dir": str(output_dir),
        "split_counts": split_counts,
        "output_files": output_paths,
        "errors": errors,
        "warnings": warnings,
    }


def print_prepare_text(result: dict[str, Any]) -> None:
    print("Teacher-distill dataset prepared." if result["ok"] else "Teacher-distill dataset preparation failed.")
    print()
    print(f"Dataset: {result['dataset_name']}")
    print(f"Stage: {result['dataset_stage']}")
    print(f"Review status: {result['review_status']}")
    print(f"Approval status: {result['approval_status']}")
    print(f"Output dir: {result['output_dir']}")
    if result["split_counts"]:
        print("Split counts:")
        for name in SPLIT_NAMES:
            print(f"- {name}: {result['split_counts'].get(name, 0)}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
