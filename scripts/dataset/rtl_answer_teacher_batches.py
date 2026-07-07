"""Local teacher-answer batch export, validation, and draft merge helpers.

All content is treated as data. These helpers never call models, execute RTL,
run testbenches, or invoke EDA tools.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from pathlib import Path
from typing import Any

from scripts.dataset.constants import ANSWER_SCHEMA_VERSION, CLAIM_LEVELS, TASK_SCHEMA_VERSION, TOOL_CHECKS
from scripts.dataset.io_utils import JsonlProblem, load_jsonl, write_jsonl


BATCH_SCHEMA_VERSION = "rtl_answer_teacher_batch_v0.1"
CREATED_BY = "export_rtl_answer_teacher_batches"
MERGE_CREATED_BY = "merge_rtl_task_answer_rows"
PROMPT_TEMPLATE_PATH = "docs/dataset/llm_rtl_answer_generation_prompt.md"
MANAGED_BATCH_NAME_RE = re.compile(r"batch_\d{3}\.json\Z")
ANSWER_REQUIRED_FIELDS = {
    "schema_version",
    "source_id",
    "task_type",
    "issue_summary",
    "time_reasoning",
    "space_reasoning",
    "safe_optimization",
    "functional_risk",
    "verification_plan",
    "claim_levels",
    "evidence_used",
    "limitations",
}
CLAIM_DOMAINS = {"correctness", "area", "activity", "power"}
DEFAULT_SYSTEM_PROMPT = (
    "You are an RTL review specialist. Use only the supplied rtl_task_v0.1 "
    "artifacts, be conservative about evidence, never invent tool results, "
    "and return grounded rtl_answer_v0.1 content for human review."
)

UNSUPPORTED_CLAIM_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "simulation": [
        re.compile(r"\bpassed\s+simulation\b", re.IGNORECASE),
        re.compile(r"\bsimulation\s+passed\b", re.IGNORECASE),
        re.compile(r"\bsimulated\s+successfully\b", re.IGNORECASE),
        re.compile(r"\bverified\s+by\s+simulation\b", re.IGNORECASE),
    ],
    "lint": [
        re.compile(r"\bpassed\s+lint\b", re.IGNORECASE),
        re.compile(r"\blint\s+passed\b", re.IGNORECASE),
        re.compile(r"\blint\s+clean\b", re.IGNORECASE),
        re.compile(r"\bno\s+lint\s+errors\b", re.IGNORECASE),
    ],
    "synthesis": [
        re.compile(r"\bsynthesized\b", re.IGNORECASE),
        re.compile(r"\bsynthesis\s+passed\b", re.IGNORECASE),
        re.compile(r"\bsynthesis\s+shows\b", re.IGNORECASE),
    ],
    "power": [
        re.compile(r"\bpower\s+(?:improved|reduced|decreased)\b", re.IGNORECASE),
        re.compile(r"\blower\s+power\b", re.IGNORECASE),
    ],
    "toggle": [
        re.compile(r"\btoggle(?:s)?\s+(?:improved|reduced|decreased)\b", re.IGNORECASE),
        re.compile(r"\bactivity\s+(?:improved|reduced|decreased)\b", re.IGNORECASE),
        re.compile(r"\bswitching\s+(?:improved|reduced|decreased)\b", re.IGNORECASE),
    ],
    "equivalence": [
        re.compile(r"\bequivalence\s+passed\b", re.IGNORECASE),
        re.compile(r"\bformal\s+(?:passed|proved)\b", re.IGNORECASE),
    ],
    "timing": [
        re.compile(r"\btiming\s+(?:passed|met|improved)\b", re.IGNORECASE),
        re.compile(r"\bmet\s+timing\b", re.IGNORECASE),
    ],
}


def _is_local_data_path(path: Path) -> bool:
    return any(part.lower() == ".local_data" for part in path.parts)


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    if not path.exists():
        return None, f"file not found: {path}"
    if path.is_symlink():
        return None, f"refusing to read symlinked JSON file: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON in {path}: line {exc.lineno} column {exc.colno}: {exc.msg}"
    except (OSError, UnicodeError) as exc:
        return None, f"could not read JSON file {path}: {exc}"


def _load_json_or_jsonl(path: Path) -> tuple[Any | list[dict[str, Any]] | None, list[str]]:
    payload, json_error = _load_json(path)
    if json_error is None:
        return payload, []
    rows, problems = load_jsonl(path)
    if rows:
        return [row for _, row in rows], []
    problem_text = [f"{path}:{problem.line or ''}: {problem.message}" for problem in problems]
    return None, [json_error, *problem_text]


def _rows_from_payload(payload: Any, *, label: str) -> tuple[list[dict[str, Any]], list[str], list[str] | None]:
    expected_source_ids: list[str] | None = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        raw_expected = payload.get("expected_source_ids")
        if isinstance(raw_expected, list) and all(isinstance(item, str) for item in raw_expected):
            expected_source_ids = list(raw_expected)
        if isinstance(payload.get("answers"), list):
            rows = payload["answers"]
        elif isinstance(payload.get("rows"), list):
            rows = payload["rows"]
        elif payload.get("schema_version") in {TASK_SCHEMA_VERSION, ANSWER_SCHEMA_VERSION}:
            rows = [payload]
        else:
            return [], [f"{label} must be a JSON array, JSONL, an object with rows/answers, or one task/answer object"], expected_source_ids
    else:
        return [], [f"{label} must be a JSON array, JSONL, or object"], expected_source_ids

    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"{label} row {index} must be a JSON object")
            continue
        normalized.append(row)
    return normalized, errors, expected_source_ids


def _load_task_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    payload, load_errors = _load_json_or_jsonl(path)
    if load_errors:
        return [], load_errors
    rows, row_errors, _ = _rows_from_payload(payload, label="tasks")
    errors = list(row_errors)
    for index, row in enumerate(rows, 1):
        if row.get("schema_version") != TASK_SCHEMA_VERSION:
            errors.append(f"task row {index} must have schema_version {TASK_SCHEMA_VERSION!r}")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"task row {index} must keep non-empty source_id")
    return rows, errors


def _load_answer_rows(path: Path) -> tuple[list[dict[str, Any]], list[str], list[str] | None]:
    payload, load_errors = _load_json_or_jsonl(path)
    if load_errors:
        return [], load_errors, None
    rows, row_errors, expected_source_ids = _rows_from_payload(payload, label="answers")
    return rows, row_errors, expected_source_ids


def _json_file_is_managed_batch(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    payload, error = _load_json(path)
    if error:
        return False
    return (
        isinstance(payload, dict)
        and payload.get("batch_schema_version") == BATCH_SCHEMA_VERSION
        and payload.get("created_by") == CREATED_BY
    )


def _managed_batch_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    return [
        path for path in sorted(output_dir.iterdir())
        if MANAGED_BATCH_NAME_RE.fullmatch(path.name) and _json_file_is_managed_batch(path)
    ]


def _prepare_output_dir(input_path: Path, output_dir: Path, planned_paths: list[Path], force: bool) -> list[str]:
    errors: list[str] = []
    try:
        resolved_input = input_path.resolve()
        resolved_output = output_dir.resolve()
    except OSError as exc:
        return [f"could not resolve input/output paths: {exc}"]
    if _is_local_data_path(resolved_output):
        errors.append("--output-dir must not be inside .local_data")
    if output_dir.exists() and output_dir.is_symlink():
        errors.append(f"--output-dir must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        errors.append(f"--output-dir exists and is not a directory: {output_dir}")
    if resolved_output == resolved_input or _is_relative_to(resolved_output, resolved_input) or _is_relative_to(resolved_input, resolved_output):
        errors.append("--output-dir must be separate from the input file/tree")
    if errors:
        return errors
    output_dir.mkdir(parents=True, exist_ok=True)

    existing_managed = _managed_batch_files(output_dir)
    if existing_managed and not force:
        names = ", ".join(path.name for path in existing_managed)
        return [f"output dir already contains managed teacher batch files: {names}; rerun with --force to replace them"]

    for planned in planned_paths:
        if not planned.exists():
            continue
        if planned.is_symlink():
            errors.append(f"managed batch file must not be a symlink: {planned}")
        elif not force:
            errors.append(f"output batch file already exists: {planned}; rerun with --force")
        elif not _json_file_is_managed_batch(planned):
            errors.append(f"existing output file is not a managed teacher batch: {planned}")
    if errors:
        return errors

    if force:
        planned_names = {path.name for path in planned_paths}
        for path in sorted(output_dir.iterdir()):
            if not MANAGED_BATCH_NAME_RE.fullmatch(path.name):
                continue
            if path.is_symlink():
                errors.append(f"managed batch file must not be a symlink: {path}")
            elif path.name not in planned_names and not _json_file_is_managed_batch(path):
                errors.append(f"refusing to replace unknown batch-like file under output dir: {path}")
        if errors:
            return errors
        for path in _managed_batch_files(output_dir):
            path.unlink()
    return []


def export_rtl_answer_teacher_batches(
    input_path: Path,
    output_dir: Path,
    batch_size: int = 5,
    limit: int | None = None,
    start_index: int = 0,
    force: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    if batch_size < 1:
        errors.append("--batch-size must be at least 1")
    if start_index < 0:
        errors.append("--start-index must be at least 0")
    if limit is not None and limit < 1:
        errors.append("--limit must be at least 1 when provided")
    if not input_path.exists():
        errors.append(f"--input does not exist: {input_path}")
    if errors:
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, 0, 0, [], errors, warnings), 1

    task_rows, task_errors = _load_task_rows(input_path)
    if task_errors:
        errors.extend(task_errors)
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, len(task_rows), 0, [], errors, warnings), 1
    windowed = task_rows[start_index:]
    if limit is not None:
        windowed = windowed[:limit]
    if not windowed:
        errors.append("no rtl_task.v0.1 rows found after applying --start-index/--limit")
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, len(task_rows), 0, [], errors, warnings), 1

    batch_count = math.ceil(len(windowed) / batch_size)
    planned_paths = [output_dir / f"batch_{index:03d}.json" for index in range(1, batch_count + 1)]
    errors.extend(_prepare_output_dir(input_path, output_dir, planned_paths, force))
    if errors:
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, len(task_rows), 0, [], errors, warnings), 1

    batch_files: list[str] = []
    for batch_index, offset in enumerate(range(0, len(windowed), batch_size), 1):
        rows = [deepcopy(row) for row in windowed[offset:offset + batch_size]]
        payload = {
            "batch_schema_version": BATCH_SCHEMA_VERSION,
            "created_by": CREATED_BY,
            "input": str(input_path),
            "batch_index": batch_index,
            "batch_count": batch_count,
            "row_count": len(rows),
            "start_index": start_index + offset,
            "prompt_template": PROMPT_TEMPLATE_PATH,
            "expected_source_ids": [row.get("source_id") for row in rows],
            "rows": rows,
        }
        path = output_dir / f"batch_{batch_index:03d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        batch_files.append(str(path))

    return _export_result(True, input_path, output_dir, batch_size, start_index, limit, len(task_rows), len(windowed), batch_files, errors, warnings), 0


def _export_result(
    ok: bool,
    input_path: Path,
    output_dir: Path,
    batch_size: int,
    start_index: int,
    limit: int | None,
    input_rows: int,
    exported_rows: int,
    batch_files: list[str],
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "input": str(input_path),
        "output_dir": str(output_dir),
        "batch_size": batch_size,
        "start_index": start_index,
        "limit": limit,
        "input_rows": input_rows,
        "exported_rows": exported_rows,
        "batch_files": batch_files,
        "prompt_template": PROMPT_TEMPLATE_PATH,
        "errors": errors,
        "warnings": warnings,
    }


def _task_by_source(tasks: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    by_source: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(tasks, 1):
        source_id = task.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"task row {index} must keep non-empty source_id")
            continue
        if source_id in by_source:
            errors.append(f"tasks contain duplicate source_id: {source_id}")
            continue
        by_source[source_id] = task
    return by_source, errors


def _answer_text(answer: dict[str, Any]) -> str:
    return json.dumps(answer, ensure_ascii=False, sort_keys=True)


def _tool_has_evidence(task: dict[str, Any], tool: str) -> bool:
    checks = task.get("tool_checks")
    if not isinstance(checks, dict):
        return False
    value = checks.get(tool)
    if value is None:
        return False
    if isinstance(value, dict):
        status = value.get("status")
        return status not in {None, "not_run"}
    return bool(value)


def _domain_has_evidence(task: dict[str, Any], domain: str) -> bool:
    tools_by_domain = {
        "correctness": ("simulation", "equivalence"),
        "area": ("synthesis",),
        "activity": ("toggle",),
        "power": ("power",),
    }
    return any(_tool_has_evidence(task, tool) for tool in tools_by_domain.get(domain, ()))


def _find_unsupported_claims(answer: dict[str, Any], task: dict[str, Any]) -> list[str]:
    text = _answer_text(answer)
    errors: list[str] = []
    for tool, patterns in UNSUPPORTED_CLAIM_PATTERNS.items():
        evidence_tool = "toggle" if tool == "timing" else tool
        has_evidence = False if tool == "timing" else _tool_has_evidence(task, evidence_tool)
        if tool == "timing":
            has_evidence = _tool_has_evidence(task, "synthesis")
        if has_evidence:
            continue
        for pattern in patterns:
            if pattern.search(text):
                errors.append(f"unsupported {tool} claim without corresponding tool_checks evidence: {pattern.pattern}")
                break
    if re.search(r"\bverified\b", text, re.IGNORECASE) and not re.search(r"\b(?:not|never|no)\s+verified\b", text, re.IGNORECASE):
        if not (_tool_has_evidence(task, "simulation") or _tool_has_evidence(task, "equivalence")):
            errors.append("unsupported verified claim without simulation/equivalence evidence")
    return errors


def _contains_task_copy(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("schema_version") == TASK_SCHEMA_VERSION:
            return True
        task_only_keys = {"prompt", "artifacts", "design_context", "constraints", "required_output"}
        if len(task_only_keys & value.keys()) >= 3:
            return True
        return any(_contains_task_copy(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_task_copy(item) for item in value)
    return False


def _has_candidate_source(task: dict[str, Any]) -> bool:
    context = task.get("design_context")
    artifacts = task.get("artifacts")
    return (
        isinstance(context, dict) and context.get("prompt_embedded_candidate_rtl") is True
    ) or (
        isinstance(artifacts, dict) and isinstance(artifacts.get("before_rtl_code"), str) and bool(artifacts["before_rtl_code"].strip())
    )


def _is_reference_only(task: dict[str, Any]) -> bool:
    context = task.get("design_context")
    role = task.get("source_rtl_role")
    if isinstance(context, dict):
        role = context.get("source_rtl_role", role)
    return role == "reference_rtl" and not _has_candidate_source(task)


def _claims_candidate_bug(answer: dict[str, Any]) -> bool:
    texts: list[str] = []
    issues = answer.get("issue_summary")
    if isinstance(issues, list):
        for issue in issues:
            if isinstance(issue, dict):
                texts.append(str(issue.get("issue", "")))
                evidence = issue.get("evidence")
                if isinstance(evidence, dict):
                    texts.append(str(evidence.get("reason", "")))
    text = "\n".join(texts).lower()
    if not text:
        return False
    if re.search(r"\b(no|not|cannot|can't|insufficient)\b.{0,80}\b(candidate|dut)\b.{0,80}\bbug", text):
        return False
    return bool(re.search(r"\b(candidate|dut)\b.{0,80}\b(bug|incorrect|wrong|fails|mismatch|broken|defect)\b", text))


def _validate_answer_row(answer: dict[str, Any], task: dict[str, Any], index: int) -> list[str]:
    prefix = f"answer row {index} source_id={answer.get('source_id')!r}"
    errors: list[str] = []
    missing = sorted(ANSWER_REQUIRED_FIELDS - answer.keys())
    if missing:
        errors.append(f"{prefix} is missing required fields: {', '.join(missing)}")
    if answer.get("schema_version") != ANSWER_SCHEMA_VERSION:
        errors.append(f"{prefix} must have schema_version {ANSWER_SCHEMA_VERSION!r}")
    if answer.get("task_type") != task.get("task_type"):
        errors.append(f"{prefix} task_type must match source task")
    if _contains_task_copy(answer):
        errors.append(f"{prefix} appears to contain a copied rtl_task.v0.1 object")

    issues = answer.get("issue_summary")
    if not isinstance(issues, list):
        errors.append(f"{prefix} issue_summary must be a list")
    for field in ("time_reasoning", "space_reasoning", "safe_optimization"):
        if field in answer and not isinstance(answer.get(field), dict):
            errors.append(f"{prefix} {field} must be an object")
    for field in ("functional_risk", "verification_plan", "evidence_used", "limitations"):
        if field in answer and not isinstance(answer.get(field), list):
            errors.append(f"{prefix} {field} must be a list")

    levels = answer.get("claim_levels")
    if not isinstance(levels, dict):
        errors.append(f"{prefix} claim_levels must be an object")
    else:
        for domain in sorted(CLAIM_DOMAINS):
            if levels.get(domain) not in CLAIM_LEVELS:
                errors.append(f"{prefix} claim_levels.{domain} has invalid value {levels.get(domain)!r}")
            if domain in {"area", "activity", "power"} and not _domain_has_evidence(task, domain):
                if levels.get(domain) != "insufficient_evidence":
                    errors.append(f"{prefix} claim_levels.{domain} must be insufficient_evidence without tool evidence")
            if levels.get(domain) == "verified" and not _domain_has_evidence(task, domain):
                errors.append(f"{prefix} claim_levels.{domain}=verified requires corresponding tool evidence")

    errors.extend(f"{prefix} {claim}" for claim in _find_unsupported_claims(answer, task))
    answer_text = _answer_text(answer).lower()
    if _is_reference_only(task) and _claims_candidate_bug(answer):
        errors.append(f"{prefix} claims a candidate DUT bug for a reference-only task")
    if _has_candidate_source(task) and "no candidate dut source is provided" in answer_text:
        errors.append(f"{prefix} says no candidate DUT source is provided despite embedded candidate RTL")
    return errors


def validate_rtl_answer_teacher_batch(
    tasks_path: Path,
    answers_path: Path,
    output_md: Path | None = None,
    output_json: Path | None = None,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    tasks, task_errors = _load_task_rows(tasks_path)
    errors.extend(task_errors)
    answers, answer_errors, expected_source_ids = _load_answer_rows(answers_path)
    errors.extend(answer_errors)
    task_by_source, task_source_errors = _task_by_source(tasks)
    errors.extend(task_source_errors)

    seen: set[str] = set()
    validated = 0
    for index, answer in enumerate(answers, 1):
        source_id = answer.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"answer row {index} is missing source_id")
            continue
        if source_id in seen:
            errors.append(f"answer row {index} duplicates source_id {source_id}")
            continue
        seen.add(source_id)
        task = task_by_source.get(source_id)
        if task is None:
            errors.append(f"answer row {index} has unknown source_id {source_id}")
            continue
        validated += 1
        errors.extend(_validate_answer_row(answer, task, index))

    if expected_source_ids is not None:
        expected_set = set(expected_source_ids)
        missing = sorted(expected_set - seen)
        extra = sorted(seen - expected_set)
        if missing:
            errors.append(f"answers missing expected source_id values: {', '.join(missing)}")
        if extra:
            errors.append(f"answers contain source_id values outside expected batch: {', '.join(extra)}")

    ok = not errors and (not strict or not warnings)
    result = _validation_result(ok, tasks_path, answers_path, len(tasks), len(answers), validated, errors, warnings)
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(_validation_markdown(result), encoding="utf-8")
    return result, 0 if ok else 1


def _validation_result(
    ok: bool,
    tasks_path: Path,
    answers_path: Path,
    task_rows: int,
    answer_rows: int,
    validated_answers: int,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tasks": str(tasks_path),
        "answers": str(answers_path),
        "task_rows": task_rows,
        "answer_rows": answer_rows,
        "validated_answers": validated_answers,
        "errors": errors,
        "warnings": warnings,
    }


def _validation_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# RTL Answer Teacher Batch Validation",
        "",
        f"- OK: {str(result['ok']).lower()}",
        f"- Task rows: {result['task_rows']}",
        f"- Answer rows: {result['answer_rows']}",
        f"- Validated answers: {result['validated_answers']}",
        f"- Errors: {len(result['errors'])}",
        f"- Warnings: {len(result['warnings'])}",
        "",
        "## Errors",
        "",
    ]
    lines.extend(f"- {error}" for error in result["errors"]) if result["errors"] else lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in result["warnings"]) if result["warnings"] else lines.append("- none")
    return "\n".join(lines) + "\n"


def merge_rtl_task_answer_rows(
    tasks_path: Path,
    answers_path: Path,
    output_path: Path,
    system_prompt_path: Path | None = None,
    strict: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []
    if _is_local_data_path(output_path.resolve()):
        errors.append("--output must not be inside .local_data")
    if any(part.lower() == "golden" for part in output_path.parts):
        errors.append("--output must not write into data/golden")
    if output_path.exists() and output_path.is_dir():
        errors.append("--output exists and is a directory")
    if errors:
        return _merge_result(False, tasks_path, answers_path, output_path, 0, errors, warnings), 1

    validation, validation_code = validate_rtl_answer_teacher_batch(tasks_path, answers_path, strict=strict)
    if validation_code != 0:
        errors.extend(validation["errors"])
        warnings.extend(validation["warnings"])
        return _merge_result(False, tasks_path, answers_path, output_path, 0, errors, warnings), 1

    tasks, task_errors = _load_task_rows(tasks_path)
    answers, answer_errors, _ = _load_answer_rows(answers_path)
    errors.extend(task_errors)
    errors.extend(answer_errors)
    task_by_source, task_source_errors = _task_by_source(tasks)
    errors.extend(task_source_errors)
    if errors:
        return _merge_result(False, tasks_path, answers_path, output_path, 0, errors, warnings), 1

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if system_prompt_path is not None:
        if system_prompt_path.is_symlink():
            return _merge_result(False, tasks_path, answers_path, output_path, 0, ["--system-prompt must not be a symlink"], warnings), 1
        try:
            system_prompt = system_prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return _merge_result(False, tasks_path, answers_path, output_path, 0, [f"could not read --system-prompt: {exc}"], warnings), 1
        if not system_prompt.strip():
            return _merge_result(False, tasks_path, answers_path, output_path, 0, ["--system-prompt must not be empty"], warnings), 1

    rows: list[dict[str, Any]] = []
    for answer in answers:
        source_id = answer.get("source_id")
        task = task_by_source.get(source_id)
        if task is None:
            continue
        rows.append({
            "source_id": source_id,
            "created_by": MERGE_CREATED_BY,
            "review_status": "draft",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": deepcopy(task)},
                {"role": "assistant", "content": deepcopy(answer)},
            ],
        })
    write_jsonl(output_path, rows)
    return _merge_result(True, tasks_path, answers_path, output_path, len(rows), errors, warnings), 0


def _merge_result(
    ok: bool,
    tasks_path: Path,
    answers_path: Path,
    output_path: Path,
    rows_written: int,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tasks": str(tasks_path),
        "answers": str(answers_path),
        "output": str(output_path),
        "rows_written": rows_written,
        "created_by": MERGE_CREATED_BY,
        "review_status": "draft",
        "errors": errors,
        "warnings": warnings,
    }


def print_export_text(result: dict[str, Any]) -> None:
    print("RTL answer teacher batches exported." if result["ok"] else "RTL answer teacher batch export failed.")
    print()
    print(f"Input: {result['input']}")
    print(f"Output dir: {result['output_dir']}")
    print(f"Input rows: {result['input_rows']}")
    print(f"Exported rows: {result['exported_rows']}")
    print(f"Batch files: {len(result['batch_files'])}")
    print(f"Prompt template: {result['prompt_template']}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def print_validation_text(result: dict[str, Any]) -> None:
    print("RTL answer teacher batch is valid." if result["ok"] else "RTL answer teacher batch is invalid.")
    print()
    print(f"Tasks: {result['tasks']}")
    print(f"Answers: {result['answers']}")
    print(f"Task rows: {result['task_rows']}")
    print(f"Answer rows: {result['answer_rows']}")
    print(f"Validated answers: {result['validated_answers']}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def print_merge_text(result: dict[str, Any]) -> None:
    print("RTL task/answer draft rows merged." if result["ok"] else "RTL task/answer merge failed.")
    print()
    print(f"Tasks: {result['tasks']}")
    print(f"Answers: {result['answers']}")
    print(f"Output: {result['output']}")
    print(f"Rows written: {result['rows_written']}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
