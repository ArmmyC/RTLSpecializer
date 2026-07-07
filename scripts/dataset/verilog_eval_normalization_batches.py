"""Local raw-batch export and validation for manual VerilogEval task normalization."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.dataset.adapters import ImportOptions, get_adapter
from scripts.dataset.constants import REQUIRED_OUTPUT, TOOL_CHECKS


BATCH_SCHEMA_VERSION = "verilog_eval_llm_normalization_batch_v0.1"
PROMPT_TEMPLATE_PATH = "docs/dataset/llm_rtl_task_normalization_prompt.md"
MANAGED_BATCH_NAME_RE = re.compile(r"batch_\d{3}\.json\Z")
PROMPT_PREFIX = "VerilogEval prompt/specification for reviewer context:\n"
ANSWER_ONLY_FIELDS = {
    "issue_summary",
    "time_reasoning",
    "space_reasoning",
    "safe_optimization",
    "functional_risk",
    "verification_plan",
    "claim_levels",
    "patch",
    "messages",
}


def _tool_checks_template() -> dict[str, None]:
    return {name: None for name in sorted(TOOL_CHECKS)}


def _is_local_data_path(path: Path) -> bool:
    return any(part.lower() == ".local_data" for part in path.parts)


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _raw_prompt_for(example: Any) -> str | None:
    prompt = example.metadata.get("raw_prompt") if isinstance(example.metadata, dict) else None
    if isinstance(prompt, str):
        return prompt
    prompt = example.artifacts.get("lint_log")
    if not isinstance(prompt, str):
        return None
    return prompt[len(PROMPT_PREFIX):] if prompt.startswith(PROMPT_PREFIX) else prompt


def _example_sort_key(example: Any) -> tuple[str, str, str, str]:
    return (
        str(example.source_id),
        json.dumps(example.metadata, sort_keys=True, default=str),
        str(example.license),
        str(example.design_family),
    )


def _rejection_row(rejection: Any) -> dict[str, Any]:
    return {
        "source_id": rejection.source_id,
        "reason": rejection.reason,
        "errors": list(rejection.errors),
        "metadata": deepcopy(rejection.metadata) if rejection.metadata else {},
    }


def _build_raw_row(example: Any) -> dict[str, Any]:
    return {
        "source_id": example.source_id,
        "source_dataset": example.source,
        "license": example.license,
        "provenance": deepcopy(example.provenance),
        "design_family": example.design_family,
        "task_type": example.task_type,
        "user_goal": example.user_goal,
        "raw_prompt": _raw_prompt_for(example),
        "raw_reference_rtl": example.artifacts.get("rtl_code"),
        "raw_testbench": example.artifacts.get("testbench"),
        "tool_checks": _tool_checks_template(),
        "notes": (
            "Normalize this row into rtl_task_v0.1 only. Preserve prompt/spec, RTL, and "
            "testbench text exactly. Keep missing tool evidence null and do not add any "
            "rtl_answer_v0.1 content."
        ),
    }


def _validate_raw_row(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("source_id", "source_dataset", "license", "design_family", "task_type", "user_goal"):
        value = row.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{field} must be a non-empty string")
    if not isinstance(row.get("provenance"), dict):
        errors.append("provenance must be an object")
    for field in ("raw_prompt", "raw_reference_rtl"):
        value = row.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{field} must be present and non-empty")
    raw_testbench = row.get("raw_testbench")
    if raw_testbench is not None and not isinstance(raw_testbench, str):
        errors.append("raw_testbench must be a string or null")
    return errors


def _json_file_is_managed_batch(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("batch_schema_version") == BATCH_SCHEMA_VERSION
        and payload.get("created_by") == "export_verilog_eval_normalization_batches"
    )


def _managed_batch_files(output_dir: Path) -> list[Path]:
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    return [
        path
        for path in sorted(output_dir.iterdir())
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
        errors.append("--output-dir must be separate from the input tree")
    if errors:
        return errors
    output_dir.mkdir(parents=True, exist_ok=True)

    existing_managed = _managed_batch_files(output_dir)
    if existing_managed and not force:
        names = ", ".join(path.name for path in existing_managed)
        return [f"output dir already contains managed batch files: {names}; rerun with --force to replace them"]

    for planned in planned_paths:
        if not planned.exists():
            continue
        if planned.is_symlink():
            errors.append(f"managed batch file must not be a symlink: {planned}")
            continue
        if not force:
            errors.append(f"output batch file already exists: {planned}; rerun with --force to replace managed batch files")
            continue
        if not _json_file_is_managed_batch(planned):
            errors.append(f"existing output file is not a managed normalization batch: {planned}")
    if errors:
        return errors

    if force:
        planned_names = {path.name for path in planned_paths}
        for path in sorted(output_dir.iterdir()):
            if not MANAGED_BATCH_NAME_RE.fullmatch(path.name):
                continue
            if path.is_symlink():
                errors.append(f"managed batch file must not be a symlink: {path}")
                continue
            if path.name not in planned_names and not _json_file_is_managed_batch(path):
                errors.append(f"refusing to replace unknown batch-like file under output dir: {path}")
        if errors:
            return errors
        for path in _managed_batch_files(output_dir):
            path.unlink()
    return []


def export_verilog_eval_normalization_batches(
    input_path: Path,
    output_dir: Path,
    batch_size: int = 10,
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
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, 0, 0, [], [], errors, warnings), 1

    try:
        adapter = get_adapter("verilog_eval")
    except ValueError as exc:
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, 0, 0, [], [], [str(exc)], warnings), 1

    discovery = adapter.discover_examples(input_path, ImportOptions())
    warnings.extend(discovery.warnings)
    rejections = [_rejection_row(item) for item in discovery.rejections]
    exportable: list[dict[str, Any]] = []
    for example in sorted(discovery.examples, key=_example_sort_key):
        row = _build_raw_row(example)
        row_errors = _validate_raw_row(row)
        if row_errors:
            rejections.append({
                "source_id": example.source_id,
                "reason": "normalization export row is incomplete",
                "errors": row_errors,
                "metadata": deepcopy(example.metadata) if isinstance(example.metadata, dict) else {},
            })
            continue
        exportable.append(row)
    windowed = exportable[start_index:]
    if limit is not None:
        windowed = windowed[:limit]
    if not windowed:
        errors.append("no exportable VerilogEval rows found after applying --start-index/--limit")
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, discovery.discovered_examples, 0, [], rejections, errors, warnings), 1

    batch_count = math.ceil(len(windowed) / batch_size)
    planned_paths = [output_dir / f"batch_{index:03d}.json" for index in range(1, batch_count + 1)]
    errors.extend(_prepare_output_dir(input_path, output_dir, planned_paths, force))
    if errors:
        return _export_result(False, input_path, output_dir, batch_size, start_index, limit, discovery.discovered_examples, 0, [], rejections, errors, warnings), 1

    batch_files: list[str] = []
    for batch_index, offset in enumerate(range(0, len(windowed), batch_size), 1):
        rows = [deepcopy(row) for row in windowed[offset:offset + batch_size]]
        payload = {
            "batch_schema_version": BATCH_SCHEMA_VERSION,
            "created_by": "export_verilog_eval_normalization_batches",
            "input": str(input_path),
            "batch_index": batch_index,
            "batch_count": batch_count,
            "row_count": len(rows),
            "start_index": start_index + offset,
            "prompt_template": PROMPT_TEMPLATE_PATH,
            "rows": rows,
        }
        path = output_dir / f"batch_{batch_index:03d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        batch_files.append(str(path))

    return _export_result(
        True,
        input_path,
        output_dir,
        batch_size,
        start_index,
        limit,
        discovery.discovered_examples,
        len(windowed),
        batch_files,
        rejections,
        errors,
        warnings,
    ), 0


def _export_result(
    ok: bool,
    input_path: Path,
    output_dir: Path,
    batch_size: int,
    start_index: int,
    limit: int | None,
    discovered_examples: int,
    exported_rows: int,
    batch_files: list[str],
    rejections: list[dict[str, Any]],
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
        "discovered_examples": discovered_examples,
        "exported_rows": exported_rows,
        "batch_files": batch_files,
        "rejected_examples": len(rejections),
        "rejections": rejections,
        "prompt_template": PROMPT_TEMPLATE_PATH,
        "errors": errors,
        "warnings": warnings,
    }


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    if not path.exists():
        return None, f"file not found: {path}"
    if path.is_symlink():
        return None, f"refusing to read symlinked JSON file: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError) as exc:
        return None, f"could not read JSON file {path}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON in {path}: line {exc.lineno} column {exc.colno}: {exc.msg}"


def _rows_from_payload(payload: Any, *, label: str) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("rows")
    else:
        return [], [f"{label} must be a JSON array or object with a rows array"]
    if not isinstance(rows, list):
        return [], [f"{label} rows must be a JSON array"]
    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append(f"{label} row {index} must be a JSON object")
            continue
        normalized.append(row)
    return normalized, errors


def validate_verilog_eval_normalized_batch(raw_batch_path: Path, normalized_path: Path) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    warnings: list[str] = []

    raw_payload, raw_error = _load_json(raw_batch_path)
    if raw_error:
        errors.append(raw_error)
        return _validation_result(False, raw_batch_path, normalized_path, 0, 0, errors, warnings), 1
    if not isinstance(raw_payload, dict) or raw_payload.get("batch_schema_version") != BATCH_SCHEMA_VERSION:
        errors.append(f"raw batch must be a {BATCH_SCHEMA_VERSION} object: {raw_batch_path}")
        return _validation_result(False, raw_batch_path, normalized_path, 0, 0, errors, warnings), 1
    raw_rows, row_errors = _rows_from_payload(raw_payload, label="raw batch")
    errors.extend(row_errors)

    normalized_payload, normalized_error = _load_json(normalized_path)
    if normalized_error:
        errors.append(normalized_error)
        return _validation_result(False, raw_batch_path, normalized_path, len(raw_rows), 0, errors, warnings), 1
    normalized_rows, normalized_row_errors = _rows_from_payload(normalized_payload, label="normalized batch")
    errors.extend(normalized_row_errors)
    if errors:
        return _validation_result(False, raw_batch_path, normalized_path, len(raw_rows), len(normalized_rows), errors, warnings), 1

    raw_by_source: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(raw_rows, 1):
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"raw batch row {index} is missing source_id")
            continue
        if source_id in raw_by_source:
            errors.append(f"raw batch contains duplicate source_id: {source_id}")
            continue
        raw_by_source[source_id] = row

    seen_source_ids: set[str] = set()
    for index, row in enumerate(normalized_rows, 1):
        source_id = row.get("source_id")
        prefix = f"normalized batch row {index}"
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"{prefix} is missing source_id")
            continue
        if source_id in seen_source_ids:
            errors.append(f"{prefix} duplicates source_id {source_id}")
            continue
        seen_source_ids.add(source_id)
        raw_row = raw_by_source.get(source_id)
        if raw_row is None:
            errors.append(f"{prefix} has unknown source_id {source_id}")
            continue

        if row.get("schema_version") != "rtl_task_v0.1":
            errors.append(f"{prefix} must have schema_version 'rtl_task_v0.1'")
        if row.get("source_dataset") != raw_row.get("source_dataset"):
            errors.append(f"{prefix} changed source_dataset for {source_id}")
        if row.get("license") != raw_row.get("license"):
            errors.append(f"{prefix} changed license for {source_id}")
        if row.get("design_family") != raw_row.get("design_family"):
            errors.append(f"{prefix} changed design_family for {source_id}")
        if row.get("task_type") != raw_row.get("task_type"):
            errors.append(f"{prefix} changed task_type for {source_id}")
        if row.get("user_goal") != raw_row.get("user_goal"):
            errors.append(f"{prefix} changed user_goal for {source_id}")
        if row.get("provenance") != raw_row.get("provenance"):
            errors.append(f"{prefix} changed provenance for {source_id}")

        for field in sorted(ANSWER_ONLY_FIELDS):
            if field in row:
                errors.append(f"{prefix} contains assistant-answer field {field!r}")
        if "rtl_answer_v0.1" in json.dumps(row, ensure_ascii=False):
            errors.append(f"{prefix} contains rtl_answer_v0.1 content")

        prompt = row.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{prefix} must keep non-empty prompt text")
        elif prompt != raw_row.get("raw_prompt"):
            errors.append(f"{prefix} changed prompt text for {source_id}")

        if row.get("domain") != "digital_rtl":
            errors.append(f"{prefix} must set domain to 'digital_rtl'")

        artifacts = row.get("artifacts")
        if not isinstance(artifacts, dict):
            errors.append(f"{prefix} must contain an artifacts object")
            continue
        if artifacts.get("rtl_code") != raw_row.get("raw_reference_rtl"):
            errors.append(f"{prefix} changed artifacts.rtl_code for {source_id}")
        raw_testbench = raw_row.get("raw_testbench")
        normalized_testbench = artifacts.get("testbench")
        if raw_testbench is None:
            if normalized_testbench not in {None, ""}:
                errors.append(f"{prefix} invented artifacts.testbench for {source_id}")
        elif normalized_testbench != raw_testbench:
            errors.append(f"{prefix} changed artifacts.testbench for {source_id}")
        for tool_field in ("lint_log", "synthesis_report", "toggle_report"):
            if artifacts.get(tool_field) not in {None, ""}:
                errors.append(f"{prefix} invented tool artifact {tool_field} for {source_id}")
        for rtl_side in ("before_rtl_code", "after_rtl_code"):
            if artifacts.get(rtl_side) not in {None, ""}:
                warnings.append(f"{prefix} includes unexpected {rtl_side}; verify this is intentional")

        required_output = row.get("required_output")
        if not isinstance(required_output, list) or not REQUIRED_OUTPUT.issubset(set(required_output)):
            errors.append(f"{prefix} must include required_output entries for rtl_task_v0.1")

    missing = sorted(set(raw_by_source) - seen_source_ids)
    extra = sorted(seen_source_ids - set(raw_by_source))
    if missing:
        errors.append(f"normalized batch is missing source_id values: {', '.join(missing)}")
    if extra:
        errors.append(f"normalized batch contains unexpected source_id values: {', '.join(extra)}")
    if len(normalized_rows) != len(raw_rows):
        errors.append(f"normalized batch row count {len(normalized_rows)} does not match raw batch row count {len(raw_rows)}")

    ok = not errors
    return _validation_result(ok, raw_batch_path, normalized_path, len(raw_rows), len(normalized_rows), errors, warnings), 0 if ok else 1


def _validation_result(
    ok: bool,
    raw_batch_path: Path,
    normalized_path: Path,
    expected_rows: int,
    normalized_rows: int,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "raw_batch": str(raw_batch_path),
        "normalized": str(normalized_path),
        "expected_rows": expected_rows,
        "normalized_rows": normalized_rows,
        "errors": errors,
        "warnings": warnings,
    }


def print_export_text(result: dict[str, Any]) -> None:
    print("VerilogEval normalization batches exported." if result["ok"] else "VerilogEval normalization batch export failed.")
    print()
    print(f"Input: {result['input']}")
    print(f"Output dir: {result['output_dir']}")
    print(f"Discovered examples: {result['discovered_examples']}")
    print(f"Exported rows: {result['exported_rows']}")
    print(f"Batch files: {len(result['batch_files'])}")
    print(f"Rejected examples: {result['rejected_examples']}")
    if result["batch_files"]:
        print("Prompt template: " + str(result["prompt_template"]))
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def print_validation_text(result: dict[str, Any]) -> None:
    print("Normalized VerilogEval task batch is valid." if result["ok"] else "Normalized VerilogEval task batch is invalid.")
    print()
    print(f"Raw batch: {result['raw_batch']}")
    print(f"Normalized: {result['normalized']}")
    print(f"Expected rows: {result['expected_rows']}")
    print(f"Normalized rows: {result['normalized_rows']}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
