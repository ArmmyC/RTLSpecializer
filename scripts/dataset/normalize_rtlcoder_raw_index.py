#!/usr/bin/env python3
"""Normalize RTLCoder raw-index rows into conservative rtl_task_v0.1 draft rows."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.constants import REQUIRED_OUTPUT, TASK_SCHEMA_VERSION, TOOL_CHECKS
from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.rtl_extract import module_names, summarize_rtl
from scripts.dataset.verilog_eval_normalization_batches import _prompt_interface_ports


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "data" / "golden"
INPUT_SCHEMA_VERSION = "rtlcoder_raw_index_v0.1"
REPORT_SCHEMA_VERSION = "rtlcoder_rtl_task_normalization_report_v0.1"
CREATED_BY = "normalize_rtlcoder_raw_index"
MARKDOWN_MARKER = f"<!-- created_by: {CREATED_BY} -->"
SOURCE_DATASET = "rtlcoder_resyn27k"
PROVENANCE_ORIGIN = "external_rtlcoder_gpt_generated_unverified"
LICENSE_PLACEHOLDER = "unconfirmed_upstream_license"
VERY_SHORT_RTL_CHARS = 80
VERY_LONG_RTL_CHARS = 6000
WARNING_MESSAGES = {
    "no_detected_module": "No module declarations were detected in artifacts.rtl_code.",
    "multiple_modules": "Multiple module declarations were detected in artifacts.rtl_code.",
    "missing_endmodule": "artifacts.rtl_code does not contain an endmodule token.",
    "very_long_rtl": f"artifacts.rtl_code is longer than {VERY_LONG_RTL_CHARS} characters.",
    "very_short_rtl": f"artifacts.rtl_code is shorter than {VERY_SHORT_RTL_CHARS} characters.",
    "suspicious_markdown_fences": "Instruction or RTL text contains suspicious markdown fences.",
    "empty_instruction": "instruction_text is empty after trimming whitespace.",
    "empty_rtl": "rtl_code is empty after trimming whitespace.",
}
SKIP_REASON_MESSAGES = {
    "schema_version_mismatch": f"Row schema_version must be {INPUT_SCHEMA_VERSION}.",
    "missing_source_id": "Row source_id must be a non-empty string.",
    "empty_instruction": WARNING_MESSAGES["empty_instruction"],
    "empty_rtl": WARNING_MESSAGES["empty_rtl"],
    "single_module_only_requires_exactly_one_module": "--single-module-only requires exactly one detected module.",
}


def _tool_checks_template() -> dict[str, None]:
    return {name: None for name in sorted(TOOL_CHECKS)}


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _ensure_reference_suffix(source_id: str) -> str:
    return source_id if source_id.endswith("_reference") else f"{source_id}_reference"


def _prepare_outputs(input_path: Path, outputs: list[Path], force: bool) -> list[str]:
    errors: list[str] = []
    try:
        resolved_input = input_path.resolve()
    except OSError as exc:
        return [f"could not resolve input path: {exc}"]
    for output in outputs:
        try:
            resolved_output = output.resolve()
        except OSError as exc:
            errors.append(f"could not resolve output path {output}: {exc}")
            continue
        if _is_relative_to(resolved_output, GOLDEN_DIR):
            errors.append(f"output must not write into data/golden: {output}")
        if any(part.lower() == ".local_data" for part in resolved_output.parts):
            errors.append(f"output must not be inside .local_data: {output}")
        if resolved_output == resolved_input:
            errors.append(f"output must not overwrite the input file: {output}")
        if output.exists() and output.is_dir():
            errors.append(f"output exists and is a directory: {output}")
        if output.exists() and output.is_symlink():
            errors.append(f"output must not be a symlink: {output}")
    if errors:
        return errors
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            continue
        if not force:
            errors.append(f"output already exists: {output}; rerun with --force to replace managed outputs")
            continue
        if output.suffix == ".jsonl" and not _jsonl_is_managed_output(output):
            errors.append(f"existing output file is not a managed RTLCoder normalization output: {output}")
        elif output.suffix == ".json" and not _json_is_managed_report(output):
            errors.append(f"existing report JSON is not a managed RTLCoder normalization report: {output}")
        elif output.suffix == ".md" and not _markdown_is_managed_report(output):
            errors.append(f"existing report Markdown is not a managed RTLCoder normalization report: {output}")
    return errors


def _jsonl_is_managed_output(path: Path) -> bool:
    loaded, problems = load_jsonl(path)
    if problems or not loaded:
        return False
    first = loaded[0][1]
    return (
        isinstance(first, dict)
        and first.get("schema_version") == TASK_SCHEMA_VERSION
        and first.get("created_by") == CREATED_BY
    )


def _json_is_managed_report(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == REPORT_SCHEMA_VERSION
        and payload.get("created_by") == CREATED_BY
    )


def _markdown_is_managed_report(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8").startswith(MARKDOWN_MARKER)
    except (OSError, UnicodeError):
        return False


def _warning_codes(instruction_text: str, rtl_code: str, detected_modules: list[str]) -> list[str]:
    warnings: list[str] = []
    if not instruction_text.strip():
        warnings.append("empty_instruction")
    if not rtl_code.strip():
        warnings.append("empty_rtl")
    if not detected_modules:
        warnings.append("no_detected_module")
    if len(detected_modules) > 1:
        warnings.append("multiple_modules")
    if rtl_code.strip() and "endmodule" not in rtl_code:
        warnings.append("missing_endmodule")
    if len(rtl_code) < VERY_SHORT_RTL_CHARS:
        warnings.append("very_short_rtl")
    if len(rtl_code) > VERY_LONG_RTL_CHARS:
        warnings.append("very_long_rtl")
    if "```" in instruction_text or "```" in rtl_code:
        warnings.append("suspicious_markdown_fences")
    return warnings


def _build_task_row(raw_row: dict[str, Any], detected_modules: list[str], warning_codes: list[str]) -> dict[str, Any]:
    instruction_text = str(raw_row["instruction_text"])
    rtl_code = str(raw_row["rtl_code"])
    module_name = detected_modules[0] if len(detected_modules) == 1 else None
    raw_source_id = str(raw_row["source_id"])
    summary = summarize_rtl({"rtl_code": rtl_code})
    summary["top_module"] = module_name
    notes = [
        "The supplied RTL is external GPT-generated reference RTL from RTLCoder Resyn27k.",
        "No candidate DUT source is provided in this normalized task.",
        "License and provenance remain unconfirmed until manual review.",
    ]
    assumptions = [
        "This row was normalized from a local RTLCoder raw review index without executing RTL or calling external services.",
        "No testbench, lint log, synthesis report, toggle report, or correctness evidence was added.",
        "The upstream RTL is external, GPT-generated, and unverified; correctness is not guaranteed.",
    ]
    return {
        "schema_version": TASK_SCHEMA_VERSION,
        "created_by": CREATED_BY,
        "source_id": _ensure_reference_suffix(raw_source_id),
        "source_dataset": SOURCE_DATASET,
        "license": LICENSE_PLACEHOLDER,
        "provenance": {
            "origin": PROVENANCE_ORIGIN,
            "public_dataset_name": "RTLCoder Resyn27k",
            "public_dataset_url": None,
            "source_commit": None,
            "notes": (
                "Normalized from RTLCoder raw review index. Upstream RTL is external, "
                "GPT-generated, unverified, and license confirmation is still required."
            ),
            "raw_source_id": raw_source_id,
        },
        "design_family": raw_row.get("rough_design_family") or "general_rtl",
        "task_type": "rtl_bug_review",
        "user_goal": "find_correctness_bug",
        "domain": "digital_rtl",
        "prompt": instruction_text,
        "source_rtl_role": "reference_rtl",
        "tool_checks": _tool_checks_template(),
        "design_context": {
            "target_domain": "digital_rtl_public_benchmark",
            "priority": ["correctness", "low_switching_activity", "low_area"],
            "timing_policy": "timing_is_constraint_not_reward",
            "source_rtl_role": "reference_rtl",
            "target_module_name": module_name,
            "rtl_module_name": module_name,
            "interface_ports_from_prompt": _prompt_interface_ports(instruction_text),
            "prompt_embedded_candidate_rtl": False,
            "prompt_embedded_context_rtl": False,
        },
        "artifacts": {
            "rtl_code": rtl_code,
            "before_rtl_code": None,
            "after_rtl_code": None,
            "testbench": None,
            "lint_log": None,
            "synthesis_report": None,
            "toggle_report": None,
        },
        "extracted_rtl_summary": summary,
        "constraints": {
            "preserve_top_level_interface": True,
            "preserve_cycle_level_behavior": True,
            "preserve_reset_behavior": True,
            "do_not_claim_power_without_power_report": True,
            "prefer_minimal_patch": True,
        },
        "notes": notes,
        "assumptions": assumptions,
        "required_output": sorted(REQUIRED_OUTPUT),
        "review_status": "draft",
        "approval_status": "not_approved",
        "promotion_allowed": False,
        "raw_index_warnings": list(raw_row.get("import_warnings") or []),
        "normalization_warnings": [WARNING_MESSAGES[code] for code in warning_codes],
    }


def normalize_rtlcoder_raw_index(
    input_path: Path,
    output_path: Path,
    report_md: Path,
    report_json: Path,
    *,
    max_rows: int | None = None,
    single_module_only: bool = False,
    force: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    if max_rows is not None and max_rows < 1:
        errors.append("--max-rows must be at least 1 when provided")
    if errors:
        return _result(False, input_path, output_path, report_md, report_json, 0, 0, 0, max_rows, single_module_only, errors, []), 1

    loaded, problems = load_jsonl(input_path)
    if problems:
        errors.extend(f"{input_path}:{problem.line or ''}: {problem.message}" for problem in problems)
        return _result(False, input_path, output_path, report_md, report_json, 0, 0, 0, max_rows, single_module_only, errors, []), 1
    if not loaded:
        return _result(False, input_path, output_path, report_md, report_json, 0, 0, 0, max_rows, single_module_only, ["input file is empty"], []), 1

    output_errors = _prepare_outputs(input_path, [output_path, report_md, report_json], force)
    if output_errors:
        return _result(False, input_path, output_path, report_md, report_json, len(loaded), 0, 0, max_rows, single_module_only, output_errors, []), 1

    selected = loaded[:max_rows] if max_rows is not None else loaded
    emitted_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    skip_reason_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    design_family_counts: Counter[str] = Counter()
    module_count_distribution: Counter[str] = Counter()

    for line_number, raw_row in selected:
        schema_version = raw_row.get("schema_version")
        source_id = raw_row.get("source_id")
        instruction_text = raw_row.get("instruction_text")
        rtl_code = raw_row.get("rtl_code")
        if not isinstance(instruction_text, str):
            instruction_text = ""
        if not isinstance(rtl_code, str):
            rtl_code = ""
        detected_modules = module_names(rtl_code)
        warning_codes = _warning_codes(instruction_text, rtl_code, detected_modules)
        warning_counts.update(warning_codes)
        module_count_distribution[str(len(detected_modules))] += 1

        skip_reasons: list[str] = []
        if schema_version != INPUT_SCHEMA_VERSION:
            skip_reasons.append("schema_version_mismatch")
        if not isinstance(source_id, str) or not source_id:
            skip_reasons.append("missing_source_id")
            source_id = f"line_{line_number}"
        if "empty_instruction" in warning_codes:
            skip_reasons.append("empty_instruction")
        if "empty_rtl" in warning_codes:
            skip_reasons.append("empty_rtl")
        if single_module_only and len(detected_modules) != 1:
            skip_reasons.append("single_module_only_requires_exactly_one_module")

        if skip_reasons:
            skip_reason_counts.update(skip_reasons)
            skipped_rows.append(
                {
                    "source_id": source_id,
                    "line_number": line_number,
                    "skip_reasons": skip_reasons,
                    "warnings": [WARNING_MESSAGES[code] for code in warning_codes],
                    "design_family": raw_row.get("rough_design_family") or "general_rtl",
                }
            )
            continue

        normalized = _build_task_row(raw_row, detected_modules, warning_codes)
        emitted_rows.append(normalized)
        design_family_counts.update([normalized["design_family"]])

    if not emitted_rows:
        errors.append("no normalized rows were emitted")
        report = _report_payload(
            input_path,
            output_path,
            report_md,
            report_json,
            len(loaded),
            len(selected),
            emitted_rows,
            skipped_rows,
            skip_reason_counts,
            warning_counts,
            design_family_counts,
            module_count_distribution,
            max_rows,
            single_module_only,
        )
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_md.write_text(_markdown_report(report), encoding="utf-8", newline="\n")
        return _result(False, input_path, output_path, report_md, report_json, len(loaded), len(emitted_rows), len(skipped_rows), max_rows, single_module_only, errors, sorted(warning_counts)), 1

    write_jsonl(output_path, emitted_rows)
    report = _report_payload(
        input_path,
        output_path,
        report_md,
        report_json,
        len(loaded),
        len(selected),
        emitted_rows,
        skipped_rows,
        skip_reason_counts,
        warning_counts,
        design_family_counts,
        module_count_distribution,
        max_rows,
        single_module_only,
    )
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_md.write_text(_markdown_report(report), encoding="utf-8", newline="\n")
    return _result(True, input_path, output_path, report_md, report_json, len(loaded), len(emitted_rows), len(skipped_rows), max_rows, single_module_only, [], sorted(warning_counts)), 0


def _report_payload(
    input_path: Path,
    output_path: Path,
    report_md: Path,
    report_json: Path,
    input_rows: int,
    processed_rows: int,
    emitted_rows: list[dict[str, Any]],
    skipped_rows: list[dict[str, Any]],
    skip_reason_counts: Counter[str],
    warning_counts: Counter[str],
    design_family_counts: Counter[str],
    module_count_distribution: Counter[str],
    max_rows: int | None,
    single_module_only: bool,
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_by": CREATED_BY,
        "input": str(input_path),
        "output": str(output_path),
        "report_md": str(report_md),
        "report_json": str(report_json),
        "input_rows": input_rows,
        "processed_rows": processed_rows,
        "emitted_rows": len(emitted_rows),
        "skipped_rows": len(skipped_rows),
        "max_rows": max_rows,
        "single_module_only": single_module_only,
        "skip_reason_counts": dict(sorted(skip_reason_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "design_family_counts": dict(sorted(design_family_counts.items())),
        "module_count_distribution": dict(sorted(module_count_distribution.items(), key=lambda item: int(item[0]))),
        "provenance_license_warning_summary": [
            "All emitted rows preserve provenance.origin=external_rtlcoder_gpt_generated_unverified.",
            f"All emitted rows use license placeholder {LICENSE_PLACEHOLDER!r} until manual confirmation.",
            "RTLCoder rows remain external, GPT-generated, and correctness is not guaranteed.",
        ],
        "skipped_row_details": skipped_rows,
    }


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        MARKDOWN_MARKER,
        "# RTLCoder rtl_task normalization report",
        "",
        "## Summary",
        "",
        f"- Input: `{report['input']}`",
        f"- Output: `{report['output']}`",
        f"- Input rows: {report['input_rows']}",
        f"- Processed rows: {report['processed_rows']}",
        f"- Emitted rows: {report['emitted_rows']}",
        f"- Skipped rows: {report['skipped_rows']}",
        f"- Max rows: {report['max_rows']}",
        f"- Single-module-only: {str(report['single_module_only']).lower()}",
        "",
        "## Skip reasons",
        "",
    ]
    if report["skip_reason_counts"]:
        lines.extend(f"- `{reason}`: {count}" for reason, count in report["skip_reason_counts"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Warning counts", ""])
    if report["warning_counts"]:
        lines.extend(f"- `{warning}`: {count}" for warning, count in report["warning_counts"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Design family counts", ""])
    if report["design_family_counts"]:
        lines.extend(f"- `{name}`: {count}" for name, count in report["design_family_counts"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Module counts", ""])
    if report["module_count_distribution"]:
        lines.extend(f"- `{count}` modules: {rows}" for count, rows in report["module_count_distribution"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Provenance and license warnings", ""])
    lines.extend(f"- {item}" for item in report["provenance_license_warning_summary"])
    lines.extend(["", "## Skipped rows", "", "| Source ID | Line | Reasons |", "| --- | --- | --- |"])
    if report["skipped_row_details"]:
        for item in report["skipped_row_details"]:
            reasons = ", ".join(item["skip_reasons"]).replace("|", "\\|")
            lines.append(f"| `{item['source_id']}` | `{item['line_number']}` | {reasons} |")
    else:
        lines.append("| — | — | none |")
    lines.extend([
        "",
        "This workflow emits local draft rtl_task_v0.1 rows only. It does not promote anything to golden, does not prove correctness, and does not add tool evidence.",
        "",
    ])
    return "\n".join(lines)


def _result(
    ok: bool,
    input_path: Path,
    output_path: Path,
    report_md: Path,
    report_json: Path,
    input_rows: int,
    emitted_rows: int,
    skipped_rows: int,
    max_rows: int | None,
    single_module_only: bool,
    errors: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "input": str(input_path),
        "output": str(output_path),
        "report_md": str(report_md),
        "report_json": str(report_json),
        "input_rows": input_rows,
        "emitted_rows": emitted_rows,
        "skipped_rows": skipped_rows,
        "max_rows": max_rows,
        "single_module_only": single_module_only,
        "errors": errors,
        "warnings": warnings,
    }


def _print_text(result: dict[str, Any]) -> None:
    print("RTLCoder raw-index normalization completed." if result["ok"] else "RTLCoder raw-index normalization failed.")
    print()
    print(f"Input: {result['input']}")
    print(f"Output: {result['output']}")
    print(f"Input rows: {result['input_rows']}")
    print(f"Emitted rows: {result['emitted_rows']}")
    print(f"Skipped rows: {result['skipped_rows']}")
    print(f"Max rows: {result['max_rows']}")
    print(f"Single-module-only: {result['single_module_only']}")
    print(f"Report JSON: {result['report_json']}")
    print(f"Report Markdown: {result['report_md']}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")
    if result["warnings"]:
        print("\nWarnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report-md", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--single-module-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="Replace only exact managed outputs created by this tool")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, exit_code = normalize_rtlcoder_raw_index(
        args.input,
        args.output,
        args.report_md,
        args.report_json,
        max_rows=args.max_rows,
        single_module_only=args.single_module_only,
        force=args.force,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
