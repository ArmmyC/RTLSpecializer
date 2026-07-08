#!/usr/bin/env python3
"""Generate deterministic synthetic buggy-candidate variants from reference rtl_task_v0.1 rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.dataset.constants import TASK_SCHEMA_VERSION
from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.dataset.rtl_extract import module_names, summarize_rtl


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "data" / "golden"
CREATED_BY = "synthesize_rtl_bug_variants"
REPORT_SCHEMA_VERSION = "rtlcoder_synthetic_bug_report_v0.1"
MARKDOWN_MARKER = f"<!-- created_by: {CREATED_BY} -->"


@dataclass(frozen=True)
class Mutation:
    bug_type: str
    mutated_rtl: str
    mutation_summary: str
    mutated_signal_names: list[str]
    mutation_confidence: str


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


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
            errors.append(f"existing output file is not a managed synthetic bug output: {output}")
        elif output.suffix == ".json" and not _json_is_managed_report(output):
            errors.append(f"existing report JSON is not a managed synthetic bug report: {output}")
        elif output.suffix == ".md" and not _markdown_is_managed_report(output):
            errors.append(f"existing report Markdown is not a managed synthetic bug report: {output}")
    return errors


def _jsonl_is_managed_output(path: Path) -> bool:
    loaded, problems = load_jsonl(path)
    if problems or not loaded:
        return False
    first = loaded[0][1]
    return isinstance(first, dict) and first.get("created_by") == CREATED_BY and first.get("synthetic_bug") is True


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


def _base_signal(name: str) -> str:
    return name.split("[", 1)[0]


def _set_notes_list(row: dict[str, Any], note: str) -> None:
    notes = row.get("notes")
    if isinstance(notes, list):
        updated = list(notes)
    elif isinstance(notes, str):
        updated = [notes]
    else:
        updated = []
    updated.append(note)
    row["notes"] = updated


def _set_assumptions_list(row: dict[str, Any], assumption: str) -> None:
    assumptions = row.get("assumptions")
    if isinstance(assumptions, list):
        updated = list(assumptions)
    elif isinstance(assumptions, str):
        updated = [assumptions]
    else:
        updated = []
    updated.append(assumption)
    row["assumptions"] = updated


def _vector_output_names(rtl: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(
        r"\boutput\b(?P<body>[^;\n)]*\[[^\]]+\][^;\n)]*)",
        rtl,
        re.IGNORECASE,
    ):
        body = match.group("body")
        body = re.sub(r"\[[^\]]+\]", " ", body)
        body = re.sub(
            r"\b(?:output|input|inout|wire|reg|logic|signed|unsigned|integer|bit)\b",
            " ",
            body,
            flags=re.IGNORECASE,
        )
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_$]*", body):
            if name not in names:
                names.append(name)
    return names


def _toggle_literal_zero_one(text: str) -> str | None:
    literal_re = re.fullmatch(r"(?:(?P<width>\d+)')(?P<base>[bBdDhHoO])(?P<digits>[0-9a-fA-F_xXzZ]+)", text)
    if literal_re:
        width = int(literal_re.group("width"))
        base = literal_re.group("base").lower()
        digits = literal_re.group("digits").replace("_", "")
        if any(ch in digits.lower() for ch in ("x", "z")):
            return None
        value = int(digits, {"b": 2, "d": 10, "h": 16, "o": 8}[base])
        new_value = 1 if value == 0 else 0
        if base == "b":
            rendered = format(new_value, "b").zfill(max(width, 1))
        elif base == "h":
            hex_width = max((width + 3) // 4, 1)
            rendered = format(new_value, "x").zfill(hex_width)
        elif base == "o":
            oct_width = max((width + 2) // 3, 1)
            rendered = format(new_value, "o").zfill(oct_width)
        else:
            rendered = str(new_value)
        return f"{width}'{base}{rendered}"
    if re.fullmatch(r"\d+", text):
        return "1" if int(text) == 0 else "0"
    return None


def _mutate_wrong_reset_polarity(rtl: str) -> Mutation | None:
    resets = summarize_rtl({"rtl_code": rtl}).get("reset_signals") or []
    for reset_name in resets:
        escaped = re.escape(reset_name)
        active_low = re.search(rf"if\s*\(\s*(?P<prefix>!|~)\s*(?P<sig>{escaped})\s*\)", rtl)
        if active_low:
            original = active_low.group(0)
            mutated = f"if ({reset_name})"
            return Mutation(
                bug_type="wrong_reset_polarity",
                mutated_rtl=rtl[:active_low.start()] + mutated + rtl[active_low.end():],
                mutation_summary=f"Changed reset polarity check from {original!r} to {mutated!r}.",
                mutated_signal_names=[reset_name],
                mutation_confidence="medium",
            )
        active_high = re.search(rf"if\s*\(\s*(?P<sig>{escaped})\s*\)", rtl)
        if active_high:
            original = active_high.group(0)
            mutated = f"if (!{reset_name})"
            return Mutation(
                bug_type="wrong_reset_polarity",
                mutated_rtl=rtl[:active_high.start()] + mutated + rtl[active_high.end():],
                mutation_summary=f"Changed reset polarity check from {original!r} to {mutated!r}.",
                mutated_signal_names=[reset_name],
                mutation_confidence="medium",
            )
    return None


def _mutate_wrong_mux_select_polarity(rtl: str) -> Mutation | None:
    select_name = r"(?:sel|select|mux|[A-Za-z_][A-Za-z0-9_$]*(?:sel|select|mux)[A-Za-z0-9_$]*)"
    ternary = re.search(
        rf"\b(?P<sig>{select_name})\s*\?\s*(?P<t>[^:;\n]+)\s*:\s*(?P<f>[^;\n]+)",
        rtl,
        re.IGNORECASE,
    )
    if ternary:
        signal = ternary.group("sig")
        mutated_expr = f"!{signal} ? {ternary.group('t').strip()} : {ternary.group('f').strip()}"
        return Mutation(
            bug_type="wrong_mux_select_polarity",
            mutated_rtl=rtl[:ternary.start()] + mutated_expr + rtl[ternary.end():],
            mutation_summary=f"Inverted mux select polarity for signal {signal}.",
            mutated_signal_names=[signal],
            mutation_confidence="medium",
        )
    branch = re.search(
        rf"if\s*\(\s*(?P<sig>{select_name})\s*\)",
        rtl,
        re.IGNORECASE,
    )
    if branch:
        signal = branch.group("sig")
        mutated = f"if (!{signal})"
        return Mutation(
            bug_type="wrong_mux_select_polarity",
            mutated_rtl=rtl[:branch.start()] + mutated + rtl[branch.end():],
            mutation_summary=f"Inverted branch select polarity for signal {signal}.",
            mutated_signal_names=[signal],
            mutation_confidence="medium",
        )
    return None


def _mutate_incomplete_comb_assignment(rtl: str) -> Mutation | None:
    if not re.search(r"\balways_comb\b|\balways\s*@\s*\*", rtl, re.IGNORECASE):
        return None
    pattern = re.search(
        r"(?P<indent>[ \t]*)else\s+(?P<sig>[A-Za-z_][A-Za-z0-9_$]*(?:\[[^\]]+\])?)\s*=\s*[^;]+;",
        rtl,
        re.MULTILINE,
    )
    if not pattern:
        return None
    signal = _base_signal(pattern.group("sig"))
    indent = pattern.group("indent")
    replacement = (
        f"{indent}else begin\n"
        f"{indent}    /* incomplete_comb_assignment removed assignment to {signal} */\n"
        f"{indent}end"
    )
    return Mutation(
        bug_type="incomplete_comb_assignment",
        mutated_rtl=rtl[:pattern.start()] + replacement + rtl[pattern.end():],
        mutation_summary=f"Removed an else-branch assignment for combinational signal {signal}.",
        mutated_signal_names=[signal],
        mutation_confidence="low",
    )


def _mutate_off_by_one_counter_limit(rtl: str) -> Mutation | None:
    pattern = re.search(
        r"(?P<sig>[A-Za-z_][A-Za-z0-9_$]*(?:count|counter|cnt|timer)[A-Za-z0-9_$]*)\s*(?P<op><=|>=|<|>)\s*(?P<rhs>[^)\n;]+)",
        rtl,
        re.IGNORECASE,
    )
    if not pattern:
        return None
    signal = pattern.group("sig")
    op = pattern.group("op")
    mutated_op = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}[op]
    replacement = f"{signal} {mutated_op} {pattern.group('rhs').strip()}"
    return Mutation(
        bug_type="off_by_one_counter_limit",
        mutated_rtl=rtl[:pattern.start()] + replacement + rtl[pattern.end():],
        mutation_summary=f"Changed counter limit comparison for {signal} from {op!r} to {mutated_op!r}.",
        mutated_signal_names=[signal],
        mutation_confidence="medium",
    )


def _mutate_shift_direction_flip(rtl: str) -> Mutation | None:
    pattern = re.search(
        r"(?P<lhs>[A-Za-z_][A-Za-z0-9_$]*(?:\[[^\]]+\])?)\s*(?P<assign><=|=)\s*(?P<expr>[^;]*?)(?P<op><<|>>)(?P<tail>[^;]*);",
        rtl,
    )
    if not pattern:
        return None
    signal = _base_signal(pattern.group("lhs"))
    old_op = pattern.group("op")
    new_op = ">>" if old_op == "<<" else "<<"
    replacement = (
        f"{pattern.group('lhs')} {pattern.group('assign')} "
        f"{pattern.group('expr')}{new_op}{pattern.group('tail')};"
    )
    return Mutation(
        bug_type="shift_direction_flip",
        mutated_rtl=rtl[:pattern.start()] + replacement + rtl[pattern.end():],
        mutation_summary=f"Flipped shift direction for assignment to {signal} from {old_op!r} to {new_op!r}.",
        mutated_signal_names=[signal],
        mutation_confidence="medium",
    )


def _mutate_blocking_nonblocking_swap(rtl: str) -> Mutation | None:
    if not re.search(r"\balways_ff\b|\balways\s*@\s*\([^)]*posedge", rtl, re.IGNORECASE):
        return None
    pattern = re.search(r"(?P<lhs>[A-Za-z_][A-Za-z0-9_$]*(?:\[[^\]]+\])?)\s*<=\s*(?P<rhs>[^;]+);", rtl)
    if not pattern:
        return None
    signal = _base_signal(pattern.group("lhs"))
    replacement = f"{pattern.group('lhs')} = {pattern.group('rhs').strip()};"
    return Mutation(
        bug_type="blocking_nonblocking_swap_in_clocked_block",
        mutated_rtl=rtl[:pattern.start()] + replacement + rtl[pattern.end():],
        mutation_summary=f"Changed a clocked nonblocking assignment on {signal} to a blocking assignment.",
        mutated_signal_names=[signal],
        mutation_confidence="medium",
    )


def _mutate_width_truncation_output(rtl: str) -> Mutation | None:
    for output_name in _vector_output_names(rtl):
        pattern = re.search(
            rf"(?P<prefix>(?:assign\s+)?{re.escape(output_name)}\s*(?:<=|=)\s*)(?P<rhs>[A-Za-z_][A-Za-z0-9_$]*)\s*;",
            rtl,
        )
        if not pattern:
            continue
        rhs = pattern.group("rhs")
        replacement = f"{pattern.group('prefix')}{rhs}[0];"
        return Mutation(
            bug_type="width_truncation_output",
            mutated_rtl=rtl[:pattern.start()] + replacement + rtl[pattern.end():],
            mutation_summary=f"Truncated the RHS driving vector output {output_name} to only bit 0.",
            mutated_signal_names=[output_name, rhs],
            mutation_confidence="low",
        )
    return None


def _mutate_wrong_fsm_reset_state(rtl: str) -> Mutation | None:
    fsm_signals = summarize_rtl({"rtl_code": rtl}).get("suspected_fsm_signals") or []
    for signal_name in fsm_signals:
        escaped = re.escape(signal_name)
        pattern = re.search(
            rf"if\s*\(\s*(?:!|~)?\s*[A-Za-z_][A-Za-z0-9_$]*\s*\)\s*(?:begin\s*)?(?P<lhs>{escaped}(?:\[[^\]]+\])?)\s*(?P<assign><=|=)\s*(?P<rhs>\d+'[bBdDhHoO][0-9a-fA-F_xXzZ]+|\d+)\s*;",
            rtl,
            re.IGNORECASE,
        )
        if not pattern:
            continue
        new_rhs = _toggle_literal_zero_one(pattern.group("rhs"))
        if not new_rhs or new_rhs == pattern.group("rhs"):
            continue
        replacement = f"{pattern.group('lhs')} {pattern.group('assign')} {new_rhs};"
        return Mutation(
            bug_type="wrong_fsm_reset_state",
            mutated_rtl=rtl[:pattern.start('lhs')] + replacement + rtl[pattern.end():],
            mutation_summary=f"Changed the reset-state assignment for {signal_name} from {pattern.group('rhs')!r} to {new_rhs!r}.",
            mutated_signal_names=[signal_name],
            mutation_confidence="low",
        )
    return None


MUTATORS: list[Callable[[str], Mutation | None]] = [
    _mutate_wrong_reset_polarity,
    _mutate_wrong_mux_select_polarity,
    _mutate_incomplete_comb_assignment,
    _mutate_off_by_one_counter_limit,
    _mutate_shift_direction_flip,
    _mutate_blocking_nonblocking_swap,
    _mutate_width_truncation_output,
    _mutate_wrong_fsm_reset_state,
]


def _candidate_mutations(rtl: str) -> list[Mutation]:
    candidates: list[Mutation] = []
    seen_types: set[str] = set()
    for mutator in MUTATORS:
        mutation = mutator(rtl)
        if mutation is None or mutation.bug_type in seen_types or mutation.mutated_rtl == rtl:
            continue
        seen_types.add(mutation.bug_type)
        candidates.append(mutation)
    return candidates


def _select_mutations(source_id: str, candidates: list[Mutation], variants_per_row: int, seed: int) -> list[Mutation]:
    ranked = sorted(
        candidates,
        key=lambda mutation: hashlib.sha256(
            f"{seed}:{source_id}:{mutation.bug_type}".encode("utf-8")
        ).hexdigest(),
    )
    return ranked[:variants_per_row]


def _mutated_source_id(source_id: str, bug_type: str) -> str:
    return f"{source_id}_synthetic_{bug_type}"


def synthesize_rtl_bug_variants(
    input_path: Path,
    output_path: Path,
    report_md: Path,
    report_json: Path,
    *,
    max_source_rows: int | None = None,
    variants_per_row: int = 1,
    seed: int = 42,
    force: bool = False,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    if max_source_rows is not None and max_source_rows < 1:
        errors.append("--max-source-rows must be at least 1 when provided")
    if variants_per_row < 1:
        errors.append("--variants-per-row must be at least 1")
    if errors:
        return _result(False, input_path, output_path, report_md, report_json, 0, 0, 0, max_source_rows, variants_per_row, seed, errors), 1

    loaded, problems = load_jsonl(input_path)
    if problems:
        errors.extend(f"{input_path}:{problem.line or ''}: {problem.message}" for problem in problems)
        return _result(False, input_path, output_path, report_md, report_json, 0, 0, 0, max_source_rows, variants_per_row, seed, errors), 1
    if not loaded:
        return _result(False, input_path, output_path, report_md, report_json, 0, 0, 0, max_source_rows, variants_per_row, seed, ["input file is empty"]), 1

    output_errors = _prepare_outputs(input_path, [output_path, report_md, report_json], force)
    if output_errors:
        return _result(False, input_path, output_path, report_md, report_json, len(loaded), 0, 0, max_source_rows, variants_per_row, seed, output_errors), 1

    selected = loaded[:max_source_rows] if max_source_rows is not None else loaded
    emitted_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    bug_type_counts: Counter[str] = Counter()
    skip_reason_counts: Counter[str] = Counter()

    for line_number, row in selected:
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            source_id = f"line_{line_number}"
            reasons = ["missing_source_id"]
        else:
            reasons = []

        if row.get("schema_version") != TASK_SCHEMA_VERSION:
            reasons.append("schema_version_mismatch")
        rtl = row.get("artifacts", {}).get("rtl_code") if isinstance(row.get("artifacts"), dict) else None
        if not isinstance(rtl, str) or not rtl.strip():
            reasons.append("missing_reference_rtl")
            rtl = ""
        if module_names(rtl) and len(module_names(rtl)) != 1:
            reasons.append("requires_single_reference_module")
        elif not module_names(rtl):
            reasons.append("requires_single_reference_module")
        artifacts = row.get("artifacts") if isinstance(row.get("artifacts"), dict) else {}
        if artifacts and artifacts.get("before_rtl_code") not in {None, ""}:
            reasons.append("candidate_rtl_already_present")
        design_context = row.get("design_context") if isinstance(row.get("design_context"), dict) else {}
        if design_context.get("prompt_embedded_candidate_rtl") is True:
            reasons.append("candidate_rtl_already_present")
        if row.get("source_rtl_role") not in {None, "reference_rtl"}:
            reasons.append("source_rtl_role_must_be_reference_rtl")

        if reasons:
            skip_reason_counts.update(reasons)
            skipped_rows.append({"source_id": source_id, "line_number": line_number, "skip_reasons": reasons})
            continue

        candidates = _candidate_mutations(rtl)
        if not candidates:
            reasons = ["no_safe_mutation_pattern"]
            skip_reason_counts.update(reasons)
            skipped_rows.append({"source_id": source_id, "line_number": line_number, "skip_reasons": reasons})
            continue

        for mutation in _select_mutations(source_id, candidates, variants_per_row, seed):
            mutated_row = deepcopy(row)
            mutated_row["created_by"] = CREATED_BY
            mutated_row["generated_by"] = CREATED_BY
            mutated_row["source_variant_of"] = source_id
            mutated_row["source_id"] = _mutated_source_id(source_id, mutation.bug_type)
            mutated_row["synthetic_bug"] = True
            mutated_row["bug_type"] = mutation.bug_type
            mutated_row["mutation_summary"] = mutation.mutation_summary
            mutated_row["mutated_signal_names"] = mutation.mutated_signal_names
            mutated_row["mutation_confidence"] = mutation.mutation_confidence
            mutated_row["seed"] = seed
            mutated_row["review_status"] = "synthetic_draft"
            mutated_row["approval_status"] = "not_approved"
            mutated_row["promotion_allowed"] = False
            mutated_row["source_rtl_role"] = "reference_rtl"
            mutated_row.setdefault("artifacts", {})
            mutated_row["artifacts"]["before_rtl_code"] = mutation.mutated_rtl
            mutated_row["artifacts"]["after_rtl_code"] = None
            if not isinstance(mutated_row.get("tool_checks"), dict):
                mutated_row["tool_checks"] = {}
            for name in sorted(mutated_row["tool_checks"]):
                mutated_row["tool_checks"][name] = None
            if isinstance(mutated_row.get("design_context"), dict):
                mutated_row["design_context"]["source_rtl_role"] = "reference_rtl"
                mutated_row["design_context"]["prompt_embedded_candidate_rtl"] = True
            _set_notes_list(
                mutated_row,
                f"Synthetic buggy candidate RTL was generated by deterministic text mutation ({mutation.bug_type}) from the reference RTL.",
            )
            _set_assumptions_list(
                mutated_row,
                "Synthetic candidate RTL was generated by text mutation only; no lint, simulation, synthesis, or formal verification was run.",
            )
            bug_type_counts.update([mutation.bug_type])
            emitted_rows.append(mutated_row)

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_by": CREATED_BY,
        "input": str(input_path),
        "output": str(output_path),
        "report_md": str(report_md),
        "report_json": str(report_json),
        "input_rows": len(loaded),
        "processed_source_rows": len(selected),
        "emitted_rows": len(emitted_rows),
        "skipped_rows": len(skipped_rows),
        "max_source_rows": max_source_rows,
        "variants_per_row": variants_per_row,
        "seed": seed,
        "bug_type_counts": dict(sorted(bug_type_counts.items())),
        "skip_reason_counts": dict(sorted(skip_reason_counts.items())),
        "skipped_row_details": skipped_rows,
    }
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_md.write_text(_markdown_report(report), encoding="utf-8", newline="\n")

    if not emitted_rows:
        return _result(False, input_path, output_path, report_md, report_json, len(loaded), len(emitted_rows), len(skipped_rows), max_source_rows, variants_per_row, seed, ["no synthetic bug rows were emitted"]), 1

    write_jsonl(output_path, emitted_rows)
    return _result(True, input_path, output_path, report_md, report_json, len(loaded), len(emitted_rows), len(skipped_rows), max_source_rows, variants_per_row, seed, []), 0


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        MARKDOWN_MARKER,
        "# RTLCoder synthetic bug report",
        "",
        "## Summary",
        "",
        f"- Input: `{report['input']}`",
        f"- Output: `{report['output']}`",
        f"- Input rows: {report['input_rows']}",
        f"- Processed source rows: {report['processed_source_rows']}",
        f"- Emitted rows: {report['emitted_rows']}",
        f"- Skipped rows: {report['skipped_rows']}",
        f"- Max source rows: {report['max_source_rows']}",
        f"- Variants per row: {report['variants_per_row']}",
        f"- Seed: {report['seed']}",
        "",
        "## Bug type counts",
        "",
    ]
    if report["bug_type_counts"]:
        lines.extend(f"- `{bug_type}`: {count}" for bug_type, count in report["bug_type_counts"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Skip reasons", ""])
    if report["skip_reason_counts"]:
        lines.extend(f"- `{reason}`: {count}" for reason, count in report["skip_reason_counts"].items())
    else:
        lines.append("- none")
    lines.extend(["", "## Skipped rows", "", "| Source ID | Line | Reasons |", "| --- | --- | --- |"])
    if report["skipped_row_details"]:
        for item in report["skipped_row_details"]:
            lines.append(
                f"| `{item['source_id']}` | `{item['line_number']}` | {', '.join(item['skip_reasons']).replace('|', '\\|')} |"
            )
    else:
        lines.append("| — | — | none |")
    lines.extend([
        "",
        "Synthetic bug rows remain local draft inputs only. They are generated by text mutation, are not proven bugs, and do not carry any tool evidence.",
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
    max_source_rows: int | None,
    variants_per_row: int,
    seed: int,
    errors: list[str],
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
        "max_source_rows": max_source_rows,
        "variants_per_row": variants_per_row,
        "seed": seed,
        "errors": errors,
    }


def _print_text(result: dict[str, Any]) -> None:
    print("Synthetic bug generation completed." if result["ok"] else "Synthetic bug generation failed.")
    print()
    print(f"Input: {result['input']}")
    print(f"Output: {result['output']}")
    print(f"Input rows: {result['input_rows']}")
    print(f"Emitted rows: {result['emitted_rows']}")
    print(f"Skipped rows: {result['skipped_rows']}")
    print(f"Max source rows: {result['max_source_rows']}")
    print(f"Variants per row: {result['variants_per_row']}")
    print(f"Seed: {result['seed']}")
    print(f"Report JSON: {result['report_json']}")
    print(f"Report Markdown: {result['report_md']}")
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"- {error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report-md", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--max-source-rows", type=int)
    parser.add_argument("--variants-per-row", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true", help="Replace only exact managed outputs created by this tool")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result, exit_code = synthesize_rtl_bug_variants(
        args.input,
        args.output,
        args.report_md,
        args.report_json,
        max_source_rows=args.max_source_rows,
        variants_per_row=args.variants_per_row,
        seed=args.seed,
        force=args.force,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_text(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
