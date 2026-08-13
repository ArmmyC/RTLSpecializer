"""Manual RTL teacher generation and RTLBench handoff helpers.

This module deliberately stops at file preparation and evidence validation.  It
does not import RTLBench, invoke a model, invoke a subprocess, or execute RTL.
All source, candidate, testbench, evidence, and prompt text is treated as
untrusted data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

from scripts.dataset.rtl_generation_preparation import (
    _asset_shape_errors,
    _task_shape_errors,
    _verify_asset_integrity,
)


PACKET_SCHEMA_VERSION = "rtl_teacher_generation_packet_v0.1"
CANDIDATE_RECORD_SCHEMA_VERSION = "rtl_teacher_candidate_record_v0.1"
CANDIDATE_SCHEMA_VERSION = "rtl_teacher_candidate_v0.1"
PLAN_SCHEMA_VERSION = "rtl_candidate_verification_plan_v0.1"
ATTEMPT_SCHEMA_VERSION = "rtl_generation_attempt_v0.1"
REPAIR_PACKET_SCHEMA_VERSION = "rtl_teacher_repair_packet_v0.1"
REPAIR_HANDOFF_BINDING_SCHEMA_VERSION = "rtl_generation_teacher_repair_binding_v0.1"
MANIFEST_SCHEMA_VERSION = "rtl_candidate_manifest_v0.1"
EVIDENCE_SCHEMA_VERSION = "rtl_candidate_evidence_v0.1"
PROFILE = "verilog_eval_mismatch_v1"
TESTBENCH_TOP = "tb"
SIMULATION_CONTRACT = "mismatch_count_v1"
REQUESTED_CHECKS = {"compile": True, "simulation": True, "lint": False, "synthesis": False}
# Teacher-generation handoff bindings are versioned with their private asset
# correction overlay. Keep rejecting older correction contracts while allowing
# each explicitly supported active overlay to reach its handoff gate.
TEACHER_GENERATION_CORRECTION_VERSIONS = frozenset({
    "assetfix_v003",
    "assetfix_v004",
    "assetfix_v005",
    "assetfix_v006",
    "assetfix_v007",
    "assetfix_v008",
    "assetfix_v008_retry_01",
    "assetfix_v009",
    "assetfix_v010",
})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RTL_BYTES = 2 * 1024 * 1024
MAX_SUMMARY_BYTES = 8 * 1024
MAX_ASSUMPTIONS = 100
MAX_ASSUMPTION_BYTES = 2 * 1024
MAX_DIAGNOSTIC_BYTES = 4096
MAX_DIAGNOSTICS = 100
MAX_JSONL_BYTES = 32 * 1024 * 1024
MAX_WORKSPACE_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_SOURCE_INDEX_LINES = 20_000
MAX_SOURCE_INDEX_LINE_BYTES = 1_024
PROMPT_GENERATION_VERSION = "llm_rtl_teacher_generation_prompt_v0.1"
PROMPT_REPAIR_VERSION = "llm_rtl_teacher_repair_prompt_v0.1"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
MODULE_RE_TEMPLATE = r"\bmodule\s+(?:automatic\s+)?{name}\b"
FAILURE_CATEGORIES = {
    "passed",
    "tool_unavailable",
    "compile_failure",
    "functional_mismatch",
    "simulation_result_missing",
    "simulation_failure",
    "timeout",
    "internal_error",
    "partial_failure",
}
# Only failures attributable to the candidate itself may produce a repair
# packet.  Asset, runner, and infrastructure outcomes must return to their
# owning workflow rather than being sent back to the RTL teacher.
REPAIRABLE_FAILURE_CATEGORIES = frozenset({
    "compile_failure",
    "functional_mismatch",
    "timeout",
})
PACKET_ID_RE = re.compile(r"^rtl_teacher_(initial|repair)_batch_(\d{4})_([0-9a-f]{12})$")
STATUS_REASONS = {
    None,
    "not_requested",
    "pending",
    "tool_unavailable",
    "compile_failure",
    "functional_mismatch",
    "simulation_result_missing",
    "simulation_failure",
    "timeout",
    "internal_error",
    "partial_failure",
}
REQUIRED_UNATTEMPTED_REASONS = {"tool_unavailable", "compile_failure", "internal_error"}
PRIVATE_KEY_NAMES = {
    "reference_rtl_path",
    "testbench_path",
    "support_files",
    "input_hashes",
    "candidate_rtl_path",
    "workspace_paths",
    "expected_hashes",
    "raw_logs",
    "toolchain",
    "verification_evidence",
    "candidate_evidence",
    "verification_plan",
}
RESPONSE_FIELDS = {"rows"}
CANDIDATE_FIELDS = {
    "schema_version",
    "task_id",
    "top_module",
    "language",
    "rtl",
    "implementation_summary",
    "assumptions",
}
RECORD_FIELDS = {
    "schema_version",
    "candidate_id",
    "task_id",
    "source_id",
    "attempt",
    "packet_id",
    "candidate_sha256",
    "candidate",
}
PLAN_FIELDS = {
    "schema_version",
    "candidate_id",
    "task_id",
    "source_id",
    "attempt",
    "top_module",
    "verification_profile",
    "testbench_top",
    "simulation_result_contract",
    "requested_checks",
    "workspace_paths",
    "expected_hashes",
    "qualification_binding",
    "teacher_generation_binding",
    "repair_binding",
}
TEACHER_GENERATION_HANDOFF_BINDING_FIELDS = {
    "schema_version",
    "teacher_generation_binding_sha256",
    "packet_validation_report_sha256",
    "task_id",
    "source_id",
    "candidate_id",
    "attempt",
    "top_module",
    "normalization_packet_sha256",
    "normalization_response_sha256",
    "qualified_task_list_sha256",
    "qualification_binding_sha256",
    "qualification_evidence_sha256",
    "qualification_runner_sidecar_sha256",
    "corrected_testbench_sha256",
    "task_record_sha256",
    "asset_record_sha256",
    "correction_version",
    "source_commit",
    "source_tree_sha256",
    "frozen_split_sha256",
    "qualification_passed",
    "reference_rtl_supplied",
    "support_files",
}
QUALIFICATION_BINDING_FIELDS = {
    "binding_schema_version",
    "qualification_source",
    "qualification_result",
    "correction_version",
    "source_commit",
    "source_tree_sha256",
    "frozen_split_sha256",
    "qualification_report_sha256",
    "qualification_manifest_sha256",
    "mutation_evidence_sha256",
    "runner_sidecar_sha256",
    "corrected_testbench_sha256",
    "task_record_sha256",
    "asset_record_sha256",
    "reference_supplied",
}
REPAIR_HANDOFF_BINDING_FIELDS = {
    "schema_version",
    "run_id",
    "task_id",
    "source_id",
    "top_module",
    "attempt",
    "candidate_id",
    "candidate_sha256",
    "previous_attempt",
    "previous_candidate_id",
    "previous_candidate_sha256",
    "previous_evidence_sha256",
    "previous_runner_sidecar_sha256",
    "previous_manifest_sha256",
    "previous_workspace_tree_sha256",
    "repair_packet_id",
    "repair_packet_sha256",
    "teacher_generation_binding_sha256",
    "packet_validation_report_sha256",
    "qualification_binding_sha256",
    "corrected_testbench_sha256",
    "source_commit",
    "source_tree_sha256",
    "frozen_split_sha256",
    "correction_version",
    "qualification_passed",
    "reference_rtl_supplied",
    "support_files",
}
ATTEMPT_FIELDS = {
    "schema_version",
    "candidate_id",
    "task_id",
    "source_id",
    "attempt",
    "top_module",
    "candidate_sha256",
    "verification_profile",
    "accepted",
    "failure_category",
    "checks",
    "mismatch_summary",
    "diagnostics",
    "toolchain",
}
EVIDENCE_FIELDS = {
    "schema_version",
    "candidate_id",
    "task_id",
    "source_id",
    "attempt",
    "top_module",
    "testbench_top",
    "simulation_result_contract",
    "requested_checks",
    "input_hashes",
    "toolchain",
    "checks",
    "mismatch_summary",
    "failure_category",
    "accepted",
    "diagnostics",
}


class WorkflowError(ValueError):
    """A deterministic preflight or validation error."""


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _report_error(value: BaseException | str) -> str:
    text = str(value)
    text = re.sub(r"(?<![A-Za-z0-9_])/(?:[^\s]+)", "<path>", text)
    text = re.sub(r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\(?:[^\s]+)", "<path>", text)
    return text


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path, *, maximum: int | None = None) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                if maximum is not None and total > maximum:
                    raise WorkflowError(f"{path} exceeds the maximum size of {maximum} bytes")
                digest.update(chunk)
            if maximum is not None and os.fstat(handle.fileno()).st_size > maximum:
                raise WorkflowError(f"{path} grew beyond the maximum size of {maximum} bytes")
    except WorkflowError:
        raise
    except OSError as exc:
        raise WorkflowError(f"could not read {path}: {exc}") from exc
    return digest.hexdigest()


def _contains_symlink(path: Path) -> bool:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return False
    return False


def _is_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve() == right.resolve():
            return True
        if left.exists() and right.exists() and os.path.samefile(left, right):
            return True
    except OSError:
        return False
    return False


def _is_hard_link(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_nlink > 1
    except OSError:
        return False


def _tree_has_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    if not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_symlink() or (child.is_dir() and _tree_has_symlink(child)):
            return True
    return False


def _is_dangerous_root(path: Path) -> bool:
    resolved = path.resolve()
    roots = {Path(resolved.anchor), Path.cwd().resolve(), Path.home().resolve()}
    return resolved in roots


def _atomic_write(path: Path, content: bytes) -> None:
    if _contains_symlink(path):
        raise WorkflowError(f"output path contains a symlink: {path}")
    _ensure_directory(path.parent, "output parent")
    if path.exists() and path.is_dir():
        raise WorkflowError(f"output is a directory: {path}")
    handle = tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".rtl-manual-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _ensure_directory(path: Path, label: str) -> Path:
    """Create a directory hierarchy one component at a time, safely."""

    requested = path.absolute()
    if _contains_symlink(requested):
        raise WorkflowError(f"{label} path contains a symlink: {path}")
    if requested.exists():
        if not requested.is_dir():
            raise WorkflowError(f"{label} is not a directory: {path}")
        return requested
    missing: list[Path] = []
    current = requested
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            raise WorkflowError(f"could not find a parent for {label}: {path}")
        current = current.parent
    if _contains_symlink(current) or not current.is_dir():
        raise WorkflowError(f"{label} existing ancestor is not a directory: {current}")
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=stat.S_IRWXU)
        except FileExistsError:
            if _contains_symlink(directory) or not directory.is_dir():
                raise WorkflowError(f"{label} became unsafe while creating: {directory}")
        except OSError as exc:
            raise WorkflowError(f"could not create {label} {directory}: {exc}") from exc
    return requested


def _read_bounded(path: Path, maximum: int | None) -> bytes:
    """Read regular input in chunks and reject before unbounded allocation."""

    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if maximum is not None and total > maximum:
                    raise WorkflowError(f"{path} exceeds the maximum size of {maximum} bytes")
                chunks.append(chunk)
            if maximum is not None and os.fstat(handle.fileno()).st_size > maximum:
                raise WorkflowError(f"{path} grew beyond the maximum size of {maximum} bytes")
    except WorkflowError:
        raise
    except OSError as exc:
        raise WorkflowError(f"could not read {path}: {exc}") from exc
    return b"".join(chunks)


def _read_bytes(path: Path, *, maximum: int | None = None) -> bytes:
    if _contains_symlink(path) or path.is_symlink():
        raise WorkflowError(f"input must not be a symlink: {path}")
    if not path.is_file():
        raise WorkflowError(f"input is not a regular file: {path}")
    return _read_bounded(path, maximum)


def _read_json(path: Path, *, maximum: int | None = None) -> Any:
    raw = _read_bytes(path, maximum=maximum)
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"{path} is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"{path} contains malformed JSON: {exc.msg}") from exc


def _load_jsonl(path: Path, *, maximum: int | None = None) -> list[dict[str, Any]]:
    raw = _read_bytes(path, maximum=maximum)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkflowError(f"{path} is not valid UTF-8: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"{path}:{number}: malformed JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise WorkflowError(f"{path}:{number}: row must be an object")
        rows.append(value)
    if not rows:
        raise WorkflowError(f"{path} contains no JSONL rows")
    return rows


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _portable_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorkflowError(f"{label} must be a non-empty relative path")
    if value.startswith("/") or WINDOWS_DRIVE_RE.match(value) or "\\" in value:
        raise WorkflowError(f"{label} must be a normalized POSIX relative path")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise WorkflowError(f"{label} contains an unsafe path component")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise WorkflowError(f"{label} must be a valid Verilog identifier")
    return value


def _strict_fields(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise WorkflowError(f"{label} has unknown fields: {unknown}")
    if missing:
        raise WorkflowError(f"{label} is missing fields: {missing}")


def _private_string_leak(value: str) -> bool:
    if "\x00" in value or ".local_data" in value:
        return True
    if re.search(r"(?:^|[\s\"'(=])/(?:[^\s/]+/)+[^\s/]*", value):
        return True
    if WINDOWS_DRIVE_RE.search(value):
        return True
    return False


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    errors = _task_shape_errors(task)
    if errors:
        raise WorkflowError("invalid rtl_generation_task_v0.1: " + "; ".join(errors))
    for key in _walk_keys(task):
        if key.lower() in PRIVATE_KEY_NAMES:
            raise WorkflowError(f"private field is not allowed in teacher packet: {key}")
    for value in _walk_strings(task):
        # Specification, ambiguity statements, and provenance are public task
        # text; they may contain arbitrary instructional prose.  The schema
        # and key checks still prevent private structured fields from escaping.
        if _private_string_leak(value) and value != task.get("specification"):
            raise WorkflowError("teacher packet contains a private/local path")
    return task


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    rows = _load_jsonl(path)
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        _public_task(row)
        task_id = row["task_id"]
        if task_id in seen:
            raise WorkflowError(f"duplicate task_id at input row {index}: {task_id}")
        seen.add(task_id)
    return rows


def _packet_digest(kind: str, task_ids: list[str], target_attempt: int, prompt_version: str) -> str:
    return _sha256_bytes(_json_bytes({
        "packet_kind": kind,
        "task_ids": task_ids,
        "target_attempt": target_attempt,
        "prompt_version": prompt_version,
    }))[:12]


def _packet_id(kind: str, number: int, task_ids: list[str], target_attempt: int, prompt_version: str) -> str:
    prefix = "initial" if kind == "initial" else "repair"
    return f"rtl_teacher_{prefix}_batch_{number:04d}_{_packet_digest(kind, task_ids, target_attempt, prompt_version)}"


def _packet_prompt_version(kind: str) -> str:
    return PROMPT_GENERATION_VERSION if kind == "initial" else PROMPT_REPAIR_VERSION


def _validate_packet_identity(packet: dict[str, Any]) -> None:
    match = PACKET_ID_RE.fullmatch(packet["packet_id"])
    if match is None:
        raise WorkflowError("packet_id does not have the exact deterministic format")
    prefix, number_text, digest = match.groups()
    kind = packet["packet_kind"]
    if prefix != kind:
        raise WorkflowError("packet_id kind does not match packet_kind")
    number = int(number_text)
    if number < 1:
        raise WorkflowError("packet_id packet index must be positive")
    task_ids = [row["task"]["task_id"] for row in packet["rows"]]
    expected = _packet_id(kind, number, task_ids, packet["target_attempt"], _packet_prompt_version(kind))
    if packet["packet_id"] != expected or digest != _packet_digest(kind, task_ids, packet["target_attempt"], _packet_prompt_version(kind)):
        raise WorkflowError("packet_id digest does not match packet contents")


def _prompt_text(name: str) -> str:
    path = Path(__file__).resolve().parents[2] / "docs" / "dataset" / name
    try:
        return path.read_text(encoding="utf-8").rstrip() + "\n"
    except (OSError, UnicodeError) as exc:
        raise WorkflowError(f"could not read prompt document {path}: {exc}") from exc


def _generation_markdown(packet: dict[str, Any]) -> str:
    return (_prompt_text("llm_rtl_teacher_generation_prompt.md")
            + "\n## Packet JSON\n\n```json\n"
            + json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n```\n\n## Required response shape\n\n"
            + "Return exactly one JSON object containing only `rows`. Each row must "
            + "contain `schema_version`, `task_id`, `top_module`, `language`, `rtl`, "
            + "`implementation_summary`, and `assumptions`.\n")


def _repair_markdown(packet: dict[str, Any]) -> str:
    return (_prompt_text("llm_rtl_teacher_repair_prompt.md")
            + "\n## Repair packet JSON\n\n```json\n"
            + json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n```\n\n## Required response shape\n\n"
            + "Return exactly one JSON object containing only `rows`, with one "
            + "complete replacement `rtl_teacher_candidate_v0.1` row per task.\n")


def _prepare_output_files(output_dir: Path, names: list[str], overwrite: bool) -> None:
    if _is_dangerous_root(output_dir):
        raise WorkflowError(f"refusing dangerous output root: {_display_path(output_dir)}")
    if _contains_symlink(output_dir) or output_dir.is_symlink():
        raise WorkflowError(f"output directory contains a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        raise WorkflowError(f"output directory is not a directory: {output_dir}")
    if not output_dir.exists():
        _ensure_directory(output_dir, "output directory")
    try:
        os.chmod(output_dir, 0o700)
    except OSError as exc:
        raise WorkflowError(f"could not secure output directory: {output_dir}") from exc
    if output_dir.exists():
        if _tree_has_symlink(output_dir):
            raise WorkflowError(f"output directory contains a symlink: {output_dir}")
        existing = {path.name for path in output_dir.iterdir()}
        managed = {"packet_" + name for name in []}  # keeps the check explicit below
        unknown = existing - set(names)
        if unknown:
            raise WorkflowError(f"output directory contains unknown files: {sorted(unknown)}")
        if existing and not overwrite:
            raise WorkflowError("output already exists; use --overwrite")
        for path in output_dir.iterdir():
            if path.is_symlink() or _is_hard_link(path):
                raise WorkflowError(f"output entry must not be a symlink or hard-link: {path}")


def _write_packet_batch(output_dir: Path, number: int, packet: dict[str, Any], markdown: str, overwrite: bool) -> tuple[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"packet_{number:04d}.json"
    md_path = output_dir / f"packet_{number:04d}.md"
    for path in (json_path, md_path):
        if path.exists() and not overwrite:
            raise WorkflowError(f"output already exists: {path}; use --overwrite")
    _atomic_write(json_path, _json_bytes(packet, pretty=True))
    _atomic_write(md_path, markdown.encode("utf-8"))
    return str(json_path), str(md_path)


def export_teacher_generation_packets(
    tasks_path: Path,
    output_dir: Path,
    *,
    batch_size: int = 1,
    limit: int | None = None,
    task_id: str | None = None,
    overwrite: bool = False,
) -> tuple[dict[str, Any], int]:
    try:
        if batch_size < 1 or (limit is not None and limit < 1):
            raise WorkflowError("batch-size and limit must be positive")
        tasks = _load_tasks(tasks_path)
        if task_id is not None:
            tasks = [task for task in tasks if task["task_id"] == task_id]
        if limit is not None:
            tasks = tasks[:limit]
        if not tasks:
            raise WorkflowError("no tasks selected")
        packet_count = (len(tasks) + batch_size - 1) // batch_size
        names = [f"packet_{index:04d}.{suffix}" for index in range(1, packet_count + 1) for suffix in ("json", "md")]
        _prepare_output_files(output_dir, names, overwrite)
        packets: list[str] = []
        markdowns: list[str] = []
        for number in range(1, packet_count + 1):
            batch = tasks[(number - 1) * batch_size:number * batch_size]
            packet = {
                "schema_version": PACKET_SCHEMA_VERSION,
                "packet_id": _packet_id("initial", number, [x["task_id"] for x in batch], 1, PROMPT_GENERATION_VERSION),
                "packet_kind": "initial",
                "target_attempt": 1,
                "row_count": len(batch),
                "rows": [{"task": task} for task in batch],
            }
            markdown = _generation_markdown(packet)
            if any(_private_string_leak(value) for value in _walk_strings(packet)):
                raise WorkflowError("private/local path found in teacher-visible packet")
            json_path, md_path = _write_packet_batch(output_dir, number, packet, markdown, overwrite)
            packets.append(json_path)
            markdowns.append(md_path)
        report = {
            "ok": True, "input_rows": len(_load_jsonl(tasks_path)), "selected_rows": len(tasks),
            "packet_count": packet_count, "packet_json": [_display_path(Path(path)) for path in packets], "packet_markdown": [_display_path(Path(path)) for path in markdowns],
            "errors": [], "warnings": [], "serialized_bytes_scanned": sum(len(path.encode("utf-8")) for path in packets + markdowns),
        }
        return report, 0
    except WorkflowError as exc:
        return {"ok": False, "errors": [_report_error(exc)], "warnings": []}, 1


def _load_packet(path: Path) -> dict[str, Any]:
    packet = _read_json(path, maximum=MAX_RESPONSE_BYTES)
    _strict_fields(packet, {"schema_version", "packet_id", "packet_kind", "target_attempt", "row_count", "rows"}, "packet")
    if packet["schema_version"] not in {PACKET_SCHEMA_VERSION, REPAIR_PACKET_SCHEMA_VERSION} or packet["packet_kind"] not in {"initial", "repair"}:
        raise WorkflowError("unsupported teacher packet schema or packet kind")
    if not isinstance(packet["packet_id"], str) or not packet["packet_id"].strip():
        raise WorkflowError("packet_id must be a non-empty string")
    if type(packet["target_attempt"]) is not int or not 1 <= packet["target_attempt"] <= 4:
        raise WorkflowError("packet target_attempt must be in 1..4")
    if type(packet["row_count"]) is not int or not isinstance(packet["rows"], list) or packet["row_count"] != len(packet["rows"]):
        raise WorkflowError("packet row_count does not match rows")
    if not packet["rows"]:
        raise WorkflowError("packet contains no rows")
    seen: set[str] = set()
    for index, item in enumerate(packet["rows"], 1):
        if not isinstance(item, dict):
            raise WorkflowError(f"packet row {index} must be an object")
        if "task" not in item or not isinstance(item["task"], dict):
            raise WorkflowError(f"packet row {index} must contain task")
        task = _public_task(item["task"])
        if task["task_id"] in seen:
            raise WorkflowError(f"duplicate packet task_id: {task['task_id']}")
        seen.add(task["task_id"])
        if packet["packet_kind"] == "initial":
            if set(item) != {"task"}:
                raise WorkflowError("initial packet rows may contain only task")
        else:
            if set(item) != {"task", "previous_candidate", "verification_feedback"}:
                raise WorkflowError("repair packet row has an invalid field set")
            if packet["target_attempt"] < 2:
                raise WorkflowError("repair packet target_attempt must be at least 2")
            _validate_candidate_object(item["previous_candidate"], task, f"repair row {index}.previous_candidate")
            feedback = item["verification_feedback"]
            _strict_fields(feedback, {"failure_category", "compile_reason", "simulation_reason", "mismatch_summary", "diagnostics"}, f"repair row {index}.verification_feedback")
            if feedback["failure_category"] not in FAILURE_CATEGORIES or feedback["failure_category"] == "passed":
                raise WorkflowError(f"repair row {index} has an invalid failure category")
            for field in ("compile_reason", "simulation_reason"):
                if feedback[field] is not None and not isinstance(feedback[field], str):
                    raise WorkflowError(f"repair row {index}.{field} must be string or null")
            _validate_mismatch(feedback["mismatch_summary"])
            if not isinstance(feedback["diagnostics"], list) or any(not isinstance(value, str) or len(value.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES for value in feedback["diagnostics"]):
                raise WorkflowError(f"repair row {index}.diagnostics is invalid")
            if any(_private_string_leak(value) for value in feedback["diagnostics"]):
                raise WorkflowError(f"repair row {index} contains private diagnostic content")
    if packet["packet_kind"] == "initial" and packet["target_attempt"] != 1:
        raise WorkflowError("initial packet target_attempt must be 1")
    if packet["packet_kind"] == "initial" and packet["schema_version"] != PACKET_SCHEMA_VERSION:
        raise WorkflowError("initial packet has an invalid schema version")
    if packet["packet_kind"] == "repair" and packet["schema_version"] != REPAIR_PACKET_SCHEMA_VERSION:
        raise WorkflowError("repair packet has an invalid schema version")
    _validate_packet_identity(packet)
    return packet


def _validate_candidate_object(candidate: Any, task: dict[str, Any], label: str) -> dict[str, Any]:
    _strict_fields(candidate, CANDIDATE_FIELDS, label)
    if candidate["schema_version"] != CANDIDATE_SCHEMA_VERSION:
        raise WorkflowError(f"{label}.schema_version is invalid")
    if candidate["task_id"] != task["task_id"]:
        raise WorkflowError(f"{label}.task_id does not match packet task")
    if candidate["top_module"] != task["top_module"] or not isinstance(task["top_module"], str):
        raise WorkflowError(f"{label}.top_module does not match packet task")
    if candidate["language"] != "systemverilog":
        raise WorkflowError(f"{label}.language must be systemverilog")
    if not isinstance(candidate["rtl"], str) or not candidate["rtl"].strip():
        raise WorkflowError(f"{label}.rtl must be non-empty")
    rtl_bytes = candidate["rtl"].encode("utf-8")
    if len(rtl_bytes) > MAX_RTL_BYTES:
        raise WorkflowError(f"{label}.rtl exceeds {MAX_RTL_BYTES} bytes")
    if "\x00" in candidate["rtl"]:
        raise WorkflowError(f"{label}.rtl contains NUL")
    if not isinstance(candidate["implementation_summary"], str) or not candidate["implementation_summary"].strip():
        raise WorkflowError(f"{label}.implementation_summary must be non-empty")
    if len(candidate["implementation_summary"].encode("utf-8")) > MAX_SUMMARY_BYTES:
        raise WorkflowError(f"{label}.implementation_summary exceeds {MAX_SUMMARY_BYTES} bytes")
    assumptions = candidate["assumptions"]
    if not isinstance(assumptions, list) or len(assumptions) > MAX_ASSUMPTIONS:
        raise WorkflowError(f"{label}.assumptions must contain at most {MAX_ASSUMPTIONS} items")
    for index, assumption in enumerate(assumptions):
        if not isinstance(assumption, str) or len(assumption.encode("utf-8")) > MAX_ASSUMPTION_BYTES:
            raise WorkflowError(f"{label}.assumptions[{index}] is invalid or too long")
    without_comments = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", candidate["rtl"], flags=re.DOTALL)
    pattern = re.compile(MODULE_RE_TEMPLATE.format(name=re.escape(task["top_module"])), re.IGNORECASE)
    if not pattern.search(without_comments):
        raise WorkflowError(f"{label}.rtl has no recognizable declaration for top module {task['top_module']}")
    for key in _walk_keys(candidate):
        if key.lower() in PRIVATE_KEY_NAMES:
            raise WorkflowError(f"{label} contains a private field")
    for value in _walk_strings(candidate):
        if _private_string_leak(value) or "workspace/" in value:
            raise WorkflowError(f"{label} contains a private/local path")
        if value != candidate["rtl"] and re.search(r"(?i)\b(?:rtlbench|mismatch_count|candidate_evidence|toolchain|raw\s+log|iverilog|verilator|yosys|vvp)\b", value):
            raise WorkflowError(f"{label} contains verification/log content")
    return candidate


def _load_assets(path: Path, private_root: Path) -> dict[str, dict[str, Any]]:
    if _contains_symlink(private_root) or private_root.is_symlink() or not private_root.is_dir():
        raise WorkflowError(f"private-assets-root is not a non-symlink directory: {private_root}")
    rows = _load_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for index, asset in enumerate(rows, 1):
        errors = _asset_shape_errors(asset)
        if errors:
            raise WorkflowError(f"private asset row {index} is invalid: {'; '.join(errors)}")
        task_id = asset["task_id"]
        if task_id in result:
            raise WorkflowError(f"duplicate private asset task_id: {task_id}")
        if asset["verification_readiness"] != "executable_ready":
            raise WorkflowError(f"asset {task_id} is not executable_ready")
        asset_paths = [asset["reference_rtl_path"], asset["testbench_path"], *asset["support_files"]]
        for field in asset_paths:
            _portable_relative(field, f"asset {task_id} path")
            target = private_root / field
            if _contains_symlink(target) or not target.is_file():
                raise WorkflowError(f"asset {task_id} path is missing or symlinked: {field}")
            if _is_hard_link(target):
                raise WorkflowError(f"asset {task_id} path is a hard-link alias: {field}")
        # The source specification hash is checked after joining the task;
        # private artifact hashes are checked here before any candidate use.
        hashes = asset["input_hashes"]
        for field, hash_field in (("reference_rtl_path", "reference_rtl_sha256"), ("testbench_path", "testbench_sha256")):
            content = _read_bytes(private_root / asset[field], maximum=MAX_WORKSPACE_ARTIFACT_BYTES)
            if _sha256_bytes(content) != hashes[hash_field]:
                raise WorkflowError(f"asset {task_id} {field} hash mismatch")
        for item in hashes["support_files"]:
            content = _read_bytes(private_root / item["path"], maximum=MAX_WORKSPACE_ARTIFACT_BYTES)
            if _sha256_bytes(content) != item["sha256"]:
                raise WorkflowError(f"asset {task_id} support hash mismatch: {item['path']}")
        artifact_paths = [private_root / asset["reference_rtl_path"], private_root / asset["testbench_path"]]
        artifact_paths.extend(private_root / value for value in asset["support_files"])
        for left_index, left in enumerate(artifact_paths):
            for right in artifact_paths[left_index + 1:]:
                if _is_alias(left, right):
                    raise WorkflowError(f"asset {task_id} artifact paths alias")
        result[task_id] = asset
    return result


def _private_leak_check(candidate: dict[str, Any], asset: dict[str, Any], private_root: Path) -> None:
    candidate_bytes = candidate["rtl"].encode("utf-8")
    private_contents = [private_root / asset["reference_rtl_path"], private_root / asset["testbench_path"]]
    private_contents.extend(private_root / item for item in asset["support_files"])
    private_hashes = {asset["input_hashes"]["reference_rtl_sha256"], asset["input_hashes"]["testbench_sha256"]}
    private_hashes.update(item["sha256"] for item in asset["input_hashes"]["support_files"])
    if _sha256_bytes(candidate_bytes) in private_hashes:
        raise WorkflowError("candidate RTL matches a private reference, testbench, or support file")
    for path in private_contents:
        content = _read_bytes(path, maximum=MAX_WORKSPACE_ARTIFACT_BYTES)
        if len(content) >= 32 and content in candidate_bytes:
            raise WorkflowError("candidate RTL contains complete private file content")


def _candidate_record(record: Any, label: str = "candidate record") -> dict[str, Any]:
    _strict_fields(record, RECORD_FIELDS, label)
    if record["schema_version"] != CANDIDATE_RECORD_SCHEMA_VERSION:
        raise WorkflowError(f"{label} has the wrong schema version")
    for field in ("candidate_id", "task_id", "source_id", "packet_id"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise WorkflowError(f"{label}.{field} must be a non-empty string")
    if not PACKET_ID_RE.fullmatch(record["packet_id"]):
        raise WorkflowError(f"{label}.packet_id is not a deterministic teacher packet ID")
    if type(record["attempt"]) is not int or not 1 <= record["attempt"] <= 4:
        raise WorkflowError(f"{label}.attempt must be in 1..4")
    if not isinstance(record["candidate_sha256"], str) or not SHA256_RE.fullmatch(record["candidate_sha256"]):
        raise WorkflowError(f"{label}.candidate_sha256 is invalid")
    candidate = record["candidate"]
    if not isinstance(candidate, dict):
        raise WorkflowError(f"{label}.candidate must be an object")
    if not isinstance(candidate.get("rtl"), str) or _sha256_bytes(candidate["rtl"].encode("utf-8")) != record["candidate_sha256"]:
        raise WorkflowError(f"{label}.candidate_sha256 does not match candidate.rtl")
    return record


def _load_candidate_records(path: Path) -> list[dict[str, Any]]:
    rows = _load_jsonl(path, maximum=MAX_JSONL_BYTES)
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, int]] = set()
    for index, record in enumerate(rows, 1):
        _candidate_record(record, f"candidate record {index}")
        if record["candidate_id"] in seen_ids or (record["task_id"], record["attempt"]) in seen_pairs:
            raise WorkflowError(f"duplicate candidate identity at row {index}")
        seen_ids.add(record["candidate_id"])
        seen_pairs.add((record["task_id"], record["attempt"]))
    return rows


def validate_teacher_candidate_batch(
    packet_path: Path,
    response_path: Path,
    *,
    private_assets_path: Path | None = None,
    private_assets_root: Path | None = None,
    output_path: Path | None = None,
    overwrite: bool = False,
    append: bool = False,
) -> tuple[dict[str, Any], int]:
    try:
        if append and overwrite:
            raise WorkflowError("--append and --overwrite are mutually exclusive")
        if private_assets_path is None or private_assets_root is None:
            raise WorkflowError("private-assets and private-assets-root are required")
        packet = _load_packet(packet_path)
        raw = _read_bytes(response_path, maximum=MAX_RESPONSE_BYTES)
        try:
            response_text = raw.decode("utf-8")
            response = json.loads(response_text)
        except UnicodeDecodeError as exc:
            raise WorkflowError(f"response is not valid UTF-8: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"response is malformed JSON: {exc.msg}") from exc
        if response_text.lstrip().startswith("```"):
            raise WorkflowError("Markdown fences are not allowed")
        _strict_fields(response, RESPONSE_FIELDS, "response")
        rows = response["rows"]
        if not isinstance(rows, list) or len(rows) != packet["row_count"]:
            raise WorkflowError("response row count does not match packet")
        assets = _load_assets(private_assets_path, private_assets_root)
        records: list[dict[str, Any]] = []
        seen_tasks: set[str] = set()
        for index, (packet_row, candidate) in enumerate(zip(packet["rows"], rows), 1):
            if not isinstance(candidate, dict):
                raise WorkflowError(f"response row {index} must be an object")
            task = packet_row["task"]
            candidate = _validate_candidate_object(candidate, task, f"response row {index}")
            if task["task_id"] in seen_tasks:
                raise WorkflowError("duplicate task IDs in response")
            seen_tasks.add(task["task_id"])
            asset = assets.get(task["task_id"])
            if asset is None:
                raise WorkflowError(f"missing private asset for task {task['task_id']}")
            if asset["source_id"] != task["source_id"] or asset["top_module"] != task["top_module"]:
                raise WorkflowError(f"task/private asset identity mismatch for {task['task_id']}")
            integrity_errors = _verify_asset_integrity(task, asset, private_assets_root)
            if integrity_errors:
                raise WorkflowError(f"private asset integrity failure: {'; '.join(integrity_errors)}")
            _private_leak_check(candidate, asset, private_assets_root)
            attempt = packet["target_attempt"]
            candidate_id = f"{task['task_id']}_attempt_{attempt:02d}"
            record = {
                "schema_version": CANDIDATE_RECORD_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "task_id": task["task_id"],
                "source_id": task["source_id"],
                "attempt": attempt,
                "packet_id": packet["packet_id"],
                "candidate_sha256": _sha256_bytes(candidate["rtl"].encode("utf-8")),
                "candidate": candidate,
            }
            records.append(record)
        if output_path is not None:
            if _is_alias(output_path, packet_path) or _is_alias(output_path, response_path) or _is_alias(output_path, private_assets_path):
                raise WorkflowError("candidate output aliases an input")
            existing: list[dict[str, Any]] = []
            if output_path.exists():
                if not (overwrite or append):
                    raise WorkflowError(f"output already exists: {output_path}; use --overwrite or --append")
                existing = _load_candidate_records(output_path)
                old_pairs = {(row["task_id"], row["attempt"]) for row in existing}
                if any((row["task_id"], row["attempt"]) in old_pairs for row in records):
                    raise WorkflowError("duplicate (task_id, attempt) in existing candidate output")
                if overwrite and not append:
                    existing = []
            combined = existing + records
            combined.sort(key=lambda row: (row["task_id"], row["attempt"], row["candidate_id"]))
            if _contains_symlink(output_path) or _is_hard_link(output_path):
                raise WorkflowError("candidate output must not be a symlink or hard-link")
            _atomic_write(output_path, b"".join(_json_bytes(row) for row in combined))
        report = {"ok": True, "packet_kind": packet["packet_kind"], "target_attempt": packet["target_attempt"], "validated_candidates": len(records), "rejected_candidates": 0, "output": _display_path(output_path), "errors": [], "warnings": []}
        return report, 0
    except (WorkflowError, UnicodeError) as exc:
        return {"ok": False, "validated_candidates": 0, "rejected_candidates": 1, "errors": [_report_error(exc)], "warnings": []}, 1


def validate_teacher_candidate_response_set(
    packet_dir: Path,
    response_dir: Path,
    *,
    private_assets_path: Path | None = None,
    private_assets_root: Path | None = None,
    output_path: Path | None = None,
    overwrite: bool = False,
) -> tuple[dict[str, Any], int]:
    """Validate a complete initial packet/response set before publication.

    Each packet is validated independently with the existing strict validator,
    but candidate records are published only after every response succeeds.
    This prevents a malformed later response from leaving a partial batch in
    ``candidate_records.jsonl``.
    """

    try:
        if private_assets_path is None or private_assets_root is None:
            raise WorkflowError("private-assets and private-assets-root are required")
        if _contains_symlink(packet_dir) or packet_dir.is_symlink() or not packet_dir.is_dir():
            raise WorkflowError("packet directory is missing, symlinked, or not a directory")
        if _contains_symlink(response_dir) or response_dir.is_symlink() or not response_dir.is_dir():
            raise WorkflowError("response directory is missing, symlinked, or not a directory")
        packet_paths = sorted(packet_dir.glob("packet_*.json"), key=lambda path: path.name)
        response_paths = sorted(response_dir.glob("packet_*_response.json"), key=lambda path: path.name)
        if not packet_paths:
            raise WorkflowError("no teacher packet JSON files found")
        if any(path.is_symlink() or not path.is_file() or _is_hard_link(path) for path in packet_paths + response_paths):
            raise WorkflowError("packet or response set contains an unsafe file")
        expected_response_names = {f"{path.stem}_response.json" for path in packet_paths}
        actual_response_names = {path.name for path in response_paths}
        if actual_response_names != expected_response_names:
            missing = sorted(expected_response_names - actual_response_names)
            extra = sorted(actual_response_names - expected_response_names)
            raise WorkflowError(f"packet/response set mismatch; missing={missing}, extra={extra}")
        if output_path is not None:
            if _is_alias(output_path, packet_dir) or _is_alias(output_path, response_dir) or _is_alias(output_path, private_assets_path):
                raise WorkflowError("candidate output aliases an input")
            if _contains_symlink(output_path) or output_path.is_symlink() or _is_hard_link(output_path):
                raise WorkflowError("candidate output must not be a symlink or hard-link")
            if output_path.exists() and not overwrite:
                raise WorkflowError(f"output already exists: {output_path}; use --overwrite")

        records: list[dict[str, Any]] = []
        packet_ids: list[str] = []
        task_ids: list[str] = []
        with tempfile.TemporaryDirectory(prefix=".candidate-batch-") as temporary:
            temporary_root = Path(temporary)
            for packet_path in packet_paths:
                response_path = response_dir / f"{packet_path.stem}_response.json"
                temporary_output = temporary_root / f"{packet_path.stem}.jsonl"
                report, code = validate_teacher_candidate_batch(
                    packet_path,
                    response_path,
                    private_assets_path=private_assets_path,
                    private_assets_root=private_assets_root,
                    output_path=temporary_output,
                )
                if code != 0 or report.get("ok") is not True:
                    raise WorkflowError(f"{packet_path.name} response failed strict validation: {report.get('errors', ['unknown validation error'])}")
                packet = _load_packet(packet_path)
                batch_records = _load_candidate_records(temporary_output)
                if packet["packet_kind"] != "initial" or packet["target_attempt"] != 1:
                    raise WorkflowError(f"{packet_path.name} is not an initial attempt-1 packet")
                if len(batch_records) != packet["row_count"]:
                    raise WorkflowError(f"{packet_path.name} record count does not match packet")
                packet_ids.append(packet["packet_id"])
                records.extend(batch_records)
                task_ids.extend(record["task_id"] for record in batch_records)

        if len(records) != len(task_ids) or len(task_ids) != len(set(task_ids)):
            raise WorkflowError("candidate response set contains duplicate task identities")
        if output_path is not None:
            _atomic_write(output_path, b"".join(_json_bytes(record) for record in records))
        return {
            "ok": True,
            "packet_count": len(packet_paths),
            "response_count": len(response_paths),
            "validated_candidates": len(records),
            "rejected_candidates": 0,
            "packet_ids": packet_ids,
            "task_ids": task_ids,
            "output": _display_path(output_path),
            "errors": [],
            "warnings": [],
        }, 0
    except (WorkflowError, UnicodeError) as exc:
        return {
            "ok": False,
            "packet_count": 0,
            "response_count": 0,
            "validated_candidates": 0,
            "rejected_candidates": 0,
            "errors": [_report_error(exc)],
            "warnings": [],
        }, 1


def _asset_for_task(asset: dict[str, Any], task: dict[str, Any], private_root: Path) -> tuple[bytes, bytes, list[tuple[str, bytes]]]:
    errors = _verify_asset_integrity(task, asset, private_root)
    if errors:
        raise WorkflowError(f"asset {task['task_id']} integrity failure: {'; '.join(errors)}")
    reference = _read_bytes(private_root / asset["reference_rtl_path"], maximum=MAX_WORKSPACE_ARTIFACT_BYTES)
    testbench = _read_bytes(private_root / asset["testbench_path"], maximum=MAX_WORKSPACE_ARTIFACT_BYTES)
    support = [(item, _read_bytes(private_root / item, maximum=MAX_WORKSPACE_ARTIFACT_BYTES)) for item in asset["support_files"]]
    return reference, testbench, support


def _supported_source(task: dict[str, Any]) -> bool:
    value = str(task.get("source_dataset", "")).casefold().replace("-", "").replace("_", "")
    return "verilogeval" in value


def _candidate_manifest_row(record: dict[str, Any], task: dict[str, Any], candidate_path: str, testbench_path: str, support_paths: list[str]) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "candidate_id": record["candidate_id"],
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": record["attempt"],
        "top_module": task["top_module"],
        "testbench_top": TESTBENCH_TOP,
        "candidate_rtl_path": candidate_path,
        "testbench_path": testbench_path,
        "support_files": support_paths,
        "simulation_result_contract": SIMULATION_CONTRACT,
        "requested_checks": dict(REQUESTED_CHECKS),
    }


def _plan_row(
    record: dict[str, Any],
    task: dict[str, Any],
    candidate_path: str,
    testbench_path: str,
    support_paths: list[str],
    hashes: dict[str, Any],
    qualification_binding: dict[str, Any] | None = None,
    teacher_generation_binding: dict[str, Any] | None = None,
    repair_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "candidate_id": record["candidate_id"],
        "task_id": task["task_id"],
        "source_id": task["source_id"],
        "attempt": record["attempt"],
        "top_module": task["top_module"],
        "verification_profile": PROFILE,
        "testbench_top": TESTBENCH_TOP,
        "simulation_result_contract": SIMULATION_CONTRACT,
        "requested_checks": dict(REQUESTED_CHECKS),
        "workspace_paths": {"candidate_rtl_path": candidate_path, "testbench_path": testbench_path, "support_files": support_paths},
        "expected_hashes": hashes,
        "qualification_binding": qualification_binding,
    }
    if teacher_generation_binding is not None:
        plan["teacher_generation_binding"] = teacher_generation_binding
    if repair_binding is not None:
        plan["repair_binding"] = repair_binding
    return plan


def _plan_qualification_binding(row: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    return {
        "binding_schema_version": "rtl_generation_qualification_binding_v0.1",
        "qualification_source": row["qualification_source"],
        "qualification_result": row["qualification_result"],
        "correction_version": report["correction_version"],
        "source_commit": report["source_commit"],
        "source_tree_sha256": report["source_tree_sha256"],
        "frozen_split_sha256": report["frozen_split_sha256"],
        "qualification_report_sha256": row["qualification_report_sha256"],
        "qualification_manifest_sha256": report["qualification_manifest_sha256"],
        "mutation_evidence_sha256": row["mutation_evidence_sha256"],
        "runner_sidecar_sha256": row["runner_sidecar_sha256"],
        "corrected_testbench_sha256": row["corrected_testbench_sha256"],
        "task_record_sha256": row["task_record_sha256"],
        "asset_record_sha256": row["asset_record_sha256"],
        "reference_supplied": row["reference_supplied"],
    }


def _validate_candidate_task_join(tasks: list[dict[str, Any]], assets: dict[str, dict[str, Any]], records: list[dict[str, Any]], private_root: Path) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes, list[tuple[str, bytes]]]]:
    task_map = {task["task_id"]: task for task in tasks}
    if set(assets) != set(task_map):
        missing = sorted(set(task_map) - set(assets))
        extra = sorted(set(assets) - set(task_map))
        raise WorkflowError(f"task/private asset one-to-one mismatch; missing={missing}, extra={extra}")
    selected: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes, list[tuple[str, bytes]]]] = []
    seen_pairs: set[tuple[str, int]] = set()
    for record in records:
        task = task_map.get(record["task_id"])
        if task is None:
            raise WorkflowError(f"candidate task does not exist: {record['task_id']}")
        _candidate_record(record)
        pair = (record["task_id"], record["attempt"])
        if pair in seen_pairs:
            raise WorkflowError(f"duplicate candidate record: {pair}")
        seen_pairs.add(pair)
        candidate = _validate_candidate_object(record["candidate"], task, f"candidate {record['candidate_id']}")
        if record["candidate_id"] != f"{record['task_id']}_attempt_{record['attempt']:02d}":
            raise WorkflowError(f"candidate ID is not deterministic: {record['candidate_id']}")
        asset = assets[record["task_id"]]
        if asset["source_id"] != task["source_id"] or asset["top_module"] != task["top_module"]:
            raise WorkflowError(f"task/asset/candidate identity mismatch: {record['task_id']}")
        if not task["top_module"] or not _supported_source(task):
            raise WorkflowError(f"unsupported or incomplete VerilogEval task: {record['task_id']}")
        reference, testbench, support = _asset_for_task(asset, task, private_root)
        if _sha256_bytes(candidate["rtl"].encode("utf-8")) != record["candidate_sha256"]:
            raise WorkflowError(f"stale candidate hash: {record['candidate_id']}")
        if _sha256_bytes(candidate["rtl"].encode("utf-8")) in {_sha256_bytes(reference), _sha256_bytes(testbench), *( _sha256_bytes(content) for _, content in support)}:
            raise WorkflowError(f"candidate matches a private verification asset: {record['candidate_id']}")
        selected.append((record, task, asset, reference, testbench, support))
    if not selected:
        raise WorkflowError("no candidate records supplied")
    return selected


def _write_staged_file(path: Path, content: bytes) -> None:
    _ensure_directory(path.parent, "staged output parent")
    if _contains_symlink(path) or path.exists():
        raise WorkflowError(f"staged output path is not new and regular: {path}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _output_aliases_inputs(output_dir: Path, paths: Iterable[Path]) -> bool:
    output = output_dir.absolute()
    for path in paths:
        candidate = path.absolute()
        if output == candidate or output in candidate.parents or candidate in output.parents:
            return True
        if _is_alias(output, candidate):
            return True
    return False


def prepare_candidate_verification(
    tasks_path: Path,
    assets_path: Path,
    private_assets_root: Path,
    candidates_path: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
    attempt: int | None = None,
    candidate_ids: Iterable[str] | None = None,
    qualification_binding_path: Path | None = None,
    teacher_generation_binding_path: Path | None = None,
    repair_binding_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    stage: Path | None = None
    backup: Path | None = None
    try:
        tasks = _load_tasks(tasks_path)
        assets = _load_assets(assets_path, private_assets_root)
        records = _load_candidate_records(candidates_path)
        qualification_report: dict[str, Any] | None = None
        qualification_rows: dict[str, dict[str, Any]] = {}
        repair_binding: dict[str, Any] | None = None
        binding_paths = [
            path for path in (
                qualification_binding_path,
                teacher_generation_binding_path,
                repair_binding_path,
            ) if path is not None
        ]
        if len(binding_paths) > 1:
            raise WorkflowError("qualification, teacher-generation, and repair bindings are mutually exclusive")
        if qualification_binding_path is not None:
            from scripts.dataset.rtl_generation_qualification_binding import validate_binding_report

            qualification_report = validate_binding_report(qualification_binding_path)
            qualification_rows = {row["task_id"]: row for row in qualification_report["rows"]}
        if repair_binding_path is not None:
            repair_binding = _read_json(repair_binding_path, maximum=MAX_RESPONSE_BYTES)
            _validate_repair_binding_object(repair_binding, "repair binding")
        if _is_dangerous_root(output_dir):
            raise WorkflowError(f"refusing dangerous output root: {_display_path(output_dir)}")
        output_parent = _ensure_directory(output_dir.parent, "output parent")
        if output_dir.is_symlink():
            raise WorkflowError(f"output directory must not be a symlink: {output_dir}")
        input_paths = (tasks_path, assets_path, candidates_path, private_assets_root)
        if qualification_binding_path is not None:
            input_paths = (*input_paths, qualification_binding_path)
        if teacher_generation_binding_path is not None:
            input_paths = (*input_paths, teacher_generation_binding_path)
        if repair_binding_path is not None:
            input_paths = (*input_paths, repair_binding_path)
        if _output_aliases_inputs(output_dir, input_paths):
            raise WorkflowError("output directory aliases an input or private asset root")
        if output_dir.exists():
            if not overwrite:
                raise WorkflowError(f"output directory already exists: {output_dir}; use --overwrite")
            if not output_dir.is_dir() or _contains_symlink(output_dir):
                raise WorkflowError("cannot overwrite non-directory or symlinked output")
            expected = {"candidate_manifest.jsonl", "verification_plan.jsonl", "run_instructions.md", "workspace"}
            existing_names = {path.name for path in output_dir.iterdir()}
            if existing_names != expected:
                raise WorkflowError("refusing to overwrite an unknown output directory")
            if _tree_has_symlink(output_dir):
                raise WorkflowError("refusing to overwrite an output containing symlinks")
        selected = _validate_candidate_task_join(tasks, assets, records, private_assets_root)
        if attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 4):
            raise WorkflowError("attempt must be an integer in 1..4")
        requested_ids = list(candidate_ids or [])
        if any(not isinstance(value, str) or not value for value in requested_ids):
            raise WorkflowError("candidate-id values must be non-empty strings")
        if len(set(requested_ids)) != len(requested_ids):
            raise WorkflowError("duplicate candidate-id filters are not allowed")
        all_ids = {row[0]["candidate_id"] for row in selected}
        unknown_ids = sorted(set(requested_ids) - all_ids)
        if unknown_ids:
            raise WorkflowError(f"unknown candidate-id: {unknown_ids}")
        if attempt is not None:
            selected = [row for row in selected if row[0]["attempt"] == attempt]
        if requested_ids:
            requested_set = set(requested_ids)
            selected = [row for row in selected if row[0]["candidate_id"] in requested_set]
        if not selected:
            raise WorkflowError("candidate selection is empty")
        stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.stage-", dir=str(output_parent)))
        if _contains_symlink(stage):
            raise WorkflowError("staging directory contains a symlink")
        if os.stat(stage).st_dev != os.stat(output_parent).st_dev:
            raise WorkflowError("staging and output parent are on different filesystems")
        if output_dir.exists() and os.stat(stage).st_dev != os.stat(output_dir).st_dev:
            raise WorkflowError("staging and output are on different filesystems")
        workspace = stage / "workspace"
        manifest_rows: list[dict[str, Any]] = []
        plan_rows: list[dict[str, Any]] = []
        reference_bytes: list[bytes] = []
        for record, task, asset, reference, testbench, support in selected:
            candidate_id = record["candidate_id"]
            candidate_path = _portable_relative(f"{candidate_id}/candidate.sv", "candidate path")
            testbench_path = _portable_relative(f"{candidate_id}/testbench.sv", "testbench path")
            support_paths = [_portable_relative(f"{candidate_id}/support/{Path(logical).name}", "support path") for logical, _ in support]
            if len(set(support_paths)) != len(support_paths):
                raise WorkflowError(f"support path collision for {candidate_id}")
            candidate_bytes = record["candidate"]["rtl"].encode("utf-8")
            hashes = {
                "candidate_rtl_sha256": _sha256_bytes(candidate_bytes),
                "testbench_sha256": _sha256_bytes(testbench),
                "support_files": [{"path": path, "sha256": _sha256_bytes(content)} for path, (_, content) in zip(support_paths, support)],
            }
            qualification_binding = None
            teacher_generation_binding = None
            if qualification_report is not None:
                binding_row = qualification_rows.get(task["task_id"])
                if binding_row is None:
                    raise WorkflowError(f"missing qualification binding for {task['task_id']}")
                if binding_row["corrected_testbench_sha256"] != hashes["testbench_sha256"]:
                    raise WorkflowError(f"qualification/testbench hash mismatch: {task['task_id']}")
                qualification_binding = _plan_qualification_binding(binding_row, qualification_report)
                _validate_qualification_binding(qualification_binding, f"qualification binding {task['task_id']}")
            if teacher_generation_binding_path is not None:
                from scripts.dataset.rtl_generation_teacher_preparation import (
                    validate_teacher_generation_handoff,
                )

                teacher_generation_binding = validate_teacher_generation_handoff(
                    run_root=teacher_generation_binding_path.resolve().parents[1],
                    binding_path=teacher_generation_binding_path,
                    tasks_path=tasks_path,
                    assets_path=assets_path,
                    private_assets_root=private_assets_root,
                    candidate_record=record,
                    task=task,
                    asset=asset,
                )
                _validate_teacher_generation_binding_object(
                    teacher_generation_binding,
                    f"teacher-generation binding {task['task_id']}",
                )
            if repair_binding is not None:
                if (
                    repair_binding["task_id"] != task["task_id"]
                    or repair_binding["source_id"] != task["source_id"]
                    or repair_binding["top_module"] != task["top_module"]
                    or repair_binding["candidate_id"] != record["candidate_id"]
                    or repair_binding["attempt"] != record["attempt"]
                ):
                    raise WorkflowError(f"repair binding identity mismatch: {task['task_id']}")
                if repair_binding["candidate_sha256"] != hashes["candidate_rtl_sha256"]:
                    raise WorkflowError(f"repair binding candidate hash mismatch: {task['task_id']}")
                if repair_binding["corrected_testbench_sha256"] != hashes["testbench_sha256"]:
                    raise WorkflowError(f"repair binding testbench hash mismatch: {task['task_id']}")
            manifest_rows.append(_candidate_manifest_row(record, task, candidate_path, testbench_path, support_paths))
            plan_rows.append(_plan_row(
                record,
                task,
                candidate_path,
                testbench_path,
                support_paths,
                hashes,
                qualification_binding,
                teacher_generation_binding,
                repair_binding,
            ))
            _write_staged_file(workspace / candidate_path, candidate_bytes)
            _write_staged_file(workspace / testbench_path, testbench)
            for path, (_, content) in zip(support_paths, support):
                _write_staged_file(workspace / path, content)
            reference_bytes.append(reference)
        _write_staged_file(stage / "candidate_manifest.jsonl", b"".join(_json_bytes(row) for row in manifest_rows))
        _write_staged_file(stage / "verification_plan.jsonl", b"".join(_json_bytes(row) for row in plan_rows))
        command_dir = str(output_dir)
        instructions = """# Manual RTLBench candidate verification\n\nRTLSpecializer prepared this workspace and did not execute RTLBench, a simulator, or any EDA tool. Run the following command manually inside a disposable, least-privileged container or VM:\n\n```bash\nrtlbench verify-candidates \\\n  --manifest %s/candidate_manifest.jsonl \\\n  --output %s/candidate_evidence.jsonl \\\n  --workspace-root %s/workspace \\\n  --work-dir /tmp/rtlbench-candidate-work \\\n  --force\n```\n\nUse an environment with no network access or production secrets. Enforce CPU, memory, process, output, disk, and time limits externally. Review the resulting evidence locally before ingestion.\n""" % (command_dir, command_dir, command_dir)
        _write_staged_file(stage / "run_instructions.md", instructions.encode("utf-8"))
        for path in stage.rglob("*"):
            if path.is_file() and path.name != "run_instructions.md":
                content = path.read_bytes()
                if any(reference and reference in content for reference in reference_bytes):
                    raise WorkflowError("reference RTL bytes would enter the RTLBench workspace")
        if output_dir.exists():
            backup = output_parent / f".{output_dir.name}.backup-{os.getpid()}"
            if backup.exists() or backup.is_symlink():
                raise WorkflowError("managed overwrite backup path already exists")
            os.replace(output_dir, backup)
        try:
            os.replace(stage, output_dir)
        except BaseException:
            if backup is not None and not output_dir.exists():
                os.replace(backup, output_dir)
                backup = None
            raise
        stage = None
        if backup is not None:
            _remove_tree(backup)
            backup = None
        report = {"ok": True, "prepared_candidates": len(selected), "manifest": _display_path(output_dir / "candidate_manifest.jsonl"), "plan": _display_path(output_dir / "verification_plan.jsonl"), "workspace": _display_path(output_dir / "workspace"), "reference_copied": False, "qualification_binding": _display_path(qualification_binding_path) if qualification_binding_path is not None else None, "teacher_generation_binding": _display_path(teacher_generation_binding_path) if teacher_generation_binding_path is not None else None, "repair_binding": _display_path(repair_binding_path) if repair_binding_path is not None else None, "errors": [], "warnings": []}
        return report, 0
    except (WorkflowError, OSError) as exc:
        if backup is not None and not output_dir.exists():
            os.replace(backup, output_dir)
        return {"ok": False, "prepared_candidates": 0, "errors": [_report_error(exc)], "warnings": []}, 1
    finally:
        if stage is not None:
            _remove_tree(stage)


def _remove_tree(path: Path) -> None:
    # Only remove directories created by this module, never caller paths.  No
    # symlink is followed while walking the staged tree.
    if not path.exists() or path.is_symlink():
        return
    if path.is_file():
        path.unlink()
        return
    for child in list(path.iterdir()):
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            _remove_tree(child)
    path.rmdir()


def _status(value: Any, label: str) -> dict[str, Any]:
    _strict_fields(value, {"attempted", "passed", "reason"}, label)
    if type(value["attempted"]) is not bool or (value["passed"] is not None and type(value["passed"]) is not bool):
        raise WorkflowError(f"{label} has invalid attempted/passed types")
    if value["attempted"] and value["passed"] is None:
        raise WorkflowError(f"{label} attempted check needs boolean passed")
    if value["attempted"] and value["passed"] is False and not value["reason"]:
        raise WorkflowError(f"{label} failed check needs a reason")
    if not value["attempted"] and value["passed"] is not None:
        raise WorkflowError(f"{label} unattempted check needs passed=null")
    if value["reason"] is not None and not isinstance(value["reason"], str):
        raise WorkflowError(f"{label}.reason must be string or null")
    if value["reason"] not in STATUS_REASONS or value["reason"] == "pending":
        raise WorkflowError(f"{label}.reason is unsupported or still pending")
    if value["attempted"] and value["passed"] and value["reason"] is not None:
        raise WorkflowError(f"{label} passing check must have reason=null")
    if not value["attempted"] and not value["reason"]:
        raise WorkflowError(f"{label} unattempted check needs a reason")
    return value


def _validate_qualification_binding(value: Any, label: str) -> dict[str, Any]:
    _strict_fields(value, QUALIFICATION_BINDING_FIELDS, label)
    if value["binding_schema_version"] != "rtl_generation_qualification_binding_v0.1":
        raise WorkflowError(f"{label} has the wrong binding schema")
    if value["qualification_source"] != "asset_qualification_retry_01" or value["qualification_result"] != "passed":
        raise WorkflowError(f"{label} is not a passed retry qualification")
    if value["correction_version"] != "assetfix_v002" or value["reference_supplied"] is not False:
        raise WorkflowError(f"{label} has an unsafe qualification binding")
    patterns = {
        "source_commit": r"^[0-9a-f]{40}$",
        "source_tree_sha256": r"^[0-9a-f]{64}$",
        "frozen_split_sha256": r"^[0-9a-f]{64}$",
        "qualification_report_sha256": r"^[0-9a-f]{64}$",
        "qualification_manifest_sha256": r"^[0-9a-f]{64}$",
        "mutation_evidence_sha256": r"^[0-9a-f]{64}$",
        "runner_sidecar_sha256": r"^[0-9a-f]{64}$",
        "corrected_testbench_sha256": r"^[0-9a-f]{64}$",
        "task_record_sha256": r"^[0-9a-f]{64}$",
        "asset_record_sha256": r"^[0-9a-f]{64}$",
    }
    for key, pattern in patterns.items():
        if not isinstance(value[key], str) or re.fullmatch(pattern, value[key]) is None:
            raise WorkflowError(f"{label}.{key} is invalid")
    return value


def _validate_teacher_generation_binding_object(value: Any, label: str) -> dict[str, Any]:
    _strict_fields(value, TEACHER_GENERATION_HANDOFF_BINDING_FIELDS, label)
    if value["schema_version"] != "rtl_generation_teacher_handoff_binding_v0.1":
        raise WorkflowError(f"{label} has the wrong binding schema")
    for field in (
        "teacher_generation_binding_sha256",
        "packet_validation_report_sha256",
        "normalization_packet_sha256",
        "normalization_response_sha256",
        "qualified_task_list_sha256",
        "qualification_binding_sha256",
        "qualification_evidence_sha256",
        "qualification_runner_sidecar_sha256",
        "corrected_testbench_sha256",
        "task_record_sha256",
        "asset_record_sha256",
        "source_tree_sha256",
        "frozen_split_sha256",
    ):
        if not isinstance(value[field], str) or SHA256_RE.fullmatch(value[field]) is None:
            raise WorkflowError(f"{label}.{field} is invalid")
    if not isinstance(value["source_commit"], str) or re.fullmatch(r"[0-9a-f]{40}", value["source_commit"]) is None:
        raise WorkflowError(f"{label}.source_commit is invalid")
    for field in ("task_id", "source_id", "candidate_id", "top_module", "correction_version"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise WorkflowError(f"{label}.{field} must be a non-empty string")
    if type(value["attempt"]) is not int or value["attempt"] != 1:
        raise WorkflowError(f"{label}.attempt must be 1")
    if value["candidate_id"] != f"{value['task_id']}_attempt_01":
        raise WorkflowError(f"{label}.candidate_id is not deterministic")
    if value["correction_version"] not in TEACHER_GENERATION_CORRECTION_VERSIONS:
        raise WorkflowError(f"{label}.correction_version is invalid")
    if value["qualification_passed"] is not True or value["reference_rtl_supplied"] is not False:
        raise WorkflowError(f"{label} has an unsafe qualification state")
    if value["support_files"] != []:
        raise WorkflowError(f"{label}.support_files must be empty")
    return value


def _validate_repair_binding_object(value: Any, label: str) -> dict[str, Any]:
    _strict_fields(value, REPAIR_HANDOFF_BINDING_FIELDS, label)
    if value["schema_version"] != REPAIR_HANDOFF_BINDING_SCHEMA_VERSION:
        raise WorkflowError(f"{label} has the wrong binding schema")
    for field in (
        "run_id",
        "task_id",
        "source_id",
        "top_module",
        "candidate_id",
        "previous_candidate_id",
        "repair_packet_id",
        "correction_version",
    ):
        if not isinstance(value[field], str) or not value[field].strip():
            raise WorkflowError(f"{label}.{field} must be a non-empty string")
    for field in (
        "candidate_sha256",
        "previous_candidate_sha256",
        "previous_evidence_sha256",
        "previous_runner_sidecar_sha256",
        "previous_manifest_sha256",
        "previous_workspace_tree_sha256",
        "repair_packet_sha256",
        "teacher_generation_binding_sha256",
        "packet_validation_report_sha256",
        "qualification_binding_sha256",
        "corrected_testbench_sha256",
        "source_tree_sha256",
        "frozen_split_sha256",
    ):
        if not isinstance(value[field], str) or SHA256_RE.fullmatch(value[field]) is None:
            raise WorkflowError(f"{label}.{field} is invalid")
    if not isinstance(value["source_commit"], str) or re.fullmatch(r"[0-9a-f]{40}", value["source_commit"]) is None:
        raise WorkflowError(f"{label}.source_commit is invalid")
    if type(value["attempt"]) is not int or not 2 <= value["attempt"] <= 4:
        raise WorkflowError(f"{label}.attempt must be in 2..4")
    if type(value["previous_attempt"]) is not int or value["previous_attempt"] != value["attempt"] - 1:
        raise WorkflowError(f"{label}.previous_attempt must immediately precede attempt")
    if value["candidate_id"] != f"{value['task_id']}_attempt_{value['attempt']:02d}":
        raise WorkflowError(f"{label}.candidate_id is not deterministic")
    if value["previous_candidate_id"] != f"{value['task_id']}_attempt_{value['previous_attempt']:02d}":
        raise WorkflowError(f"{label}.previous_candidate_id is not deterministic")
    if value["qualification_passed"] is not True or value["reference_rtl_supplied"] is not False:
        raise WorkflowError(f"{label} has an unsafe qualification state")
    if value["support_files"] != []:
        raise WorkflowError(f"{label}.support_files must be empty")
    return value


def create_teacher_repair_binding(
    tasks_path: Path,
    assets_path: Path,
    private_assets_root: Path,
    candidates_path: Path,
    attempts_path: Path,
    prior_evidence_path: Path,
    repair_packet_path: Path,
    teacher_generation_binding_path: Path,
    output_path: Path,
    *,
    candidate_id: str,
    previous_workspace_tree_sha256: str,
) -> tuple[dict[str, Any], int]:
    """Create one immutable lineage binding for a repair handoff.

    This is deliberately separate from the initial teacher-generation binding:
    that binding is attempt-1-specific, while repair handoffs must retain the
    prior failure and bind the replacement candidate to it.
    """

    try:
        tasks = _load_tasks(tasks_path)
        assets = _load_assets(assets_path, private_assets_root)
        candidates = _load_candidate_records(candidates_path)
        attempts = _load_attempts(attempts_path)
        _validate_attempt_history(attempts)
        task_by_id = {task["task_id"]: task for task in tasks}
        candidate = next((row for row in candidates if row["candidate_id"] == candidate_id), None)
        if candidate is None:
            raise WorkflowError(f"missing repair candidate: {candidate_id}")
        repair_attempt = candidate["attempt"]
        if not 2 <= repair_attempt <= 4:
            raise WorkflowError("repair binding candidate must be an attempt from 2 through 4")
        task = task_by_id.get(candidate["task_id"])
        if task is None or candidate["source_id"] != task["source_id"]:
            raise WorkflowError("repair candidate/task identity mismatch")
        asset = assets.get(task["task_id"])
        if asset is None:
            raise WorkflowError("repair task has no private asset")
        previous_attempt_number = repair_attempt - 1
        previous_candidate_id = f"{task['task_id']}_attempt_{previous_attempt_number:02d}"
        previous_candidate = next((row for row in candidates if row["candidate_id"] == previous_candidate_id), None)
        if previous_candidate is None or previous_candidate["attempt"] != previous_attempt_number:
            raise WorkflowError("missing immediately preceding candidate for repair lineage")
        previous_attempt = next((row for row in attempts if row["candidate_id"] == previous_candidate_id), None)
        if previous_attempt is None or previous_attempt["attempt"] != previous_attempt_number or previous_attempt["accepted"] is not False:
            raise WorkflowError("immediately preceding repair source is missing or accepted")
        if previous_attempt["failure_category"] not in REPAIRABLE_FAILURE_CATEGORIES:
            raise WorkflowError("immediately preceding failure is not candidate-repairable")

        evidence_rows = _load_jsonl(prior_evidence_path, maximum=MAX_JSONL_BYTES)
        evidence = next((row for row in evidence_rows if row.get("candidate_id") == previous_candidate_id), None)
        if evidence is None:
            raise WorkflowError("immediately preceding evidence row is missing")
        if evidence.get("accepted") is not False or evidence.get("failure_category") != previous_attempt["failure_category"]:
            raise WorkflowError("immediately preceding evidence does not match repair source")

        packet = _load_packet(repair_packet_path)
        if packet["packet_kind"] != "repair" or packet["target_attempt"] != repair_attempt or packet["row_count"] != 1:
            raise WorkflowError("repair packet does not target the candidate attempt")
        packet_task = packet["rows"][0]["task"]
        if packet_task != task or packet["rows"][0]["previous_candidate"] != previous_candidate["candidate"]:
            raise WorkflowError("repair packet does not bind the exact failed candidate")

        teacher_binding = _read_json(teacher_generation_binding_path, maximum=MAX_RESPONSE_BYTES)
        if teacher_binding.get("schema_version") != "rtl_generation_teacher_binding_v0.1":
            raise WorkflowError("teacher-generation binding has an invalid schema")
        packet_validation_path = teacher_generation_binding_path.parent / "teacher_generation_packet_validation.json"
        packet_validation = _read_json(packet_validation_path, maximum=MAX_RESPONSE_BYTES)
        teacher_binding_hash = _sha256_file(teacher_generation_binding_path)
        packet_validation_hash = _sha256_file(packet_validation_path)
        if packet_validation.get("ok") is not True or packet_validation.get("teacher_response_allowed") is not True:
            raise WorkflowError("teacher-generation packet validation has not passed")
        if packet_validation.get("binding_sha256") != teacher_binding_hash:
            raise WorkflowError("teacher-generation packet validation binding mismatch")
        qualified_order = teacher_binding.get("qualified_order")
        if not isinstance(qualified_order, list) or not any(
            row.get("task_id") == task["task_id"]
            and row.get("source_id") == task["source_id"]
            and row.get("top_module") == task["top_module"]
            for row in qualified_order
            if isinstance(row, dict)
        ):
            raise WorkflowError("repair task is not in the qualified teacher order")
        if teacher_binding.get("qualification_passed") is not True or teacher_binding.get("reference_rtl_supplied") is not False:
            raise WorkflowError("teacher-generation binding is not private-safe")
        if teacher_binding.get("support_file_count") != 0:
            raise WorkflowError("teacher-generation binding declares support files")

        _, testbench, support = _asset_for_task(asset, task, private_assets_root)
        if support:
            raise WorkflowError("repair asset declares support files")
        candidate_hash = candidate["candidate_sha256"]
        previous_candidate_hash = previous_candidate["candidate_sha256"]
        if candidate_hash == previous_candidate_hash:
            raise WorkflowError("repair candidate is byte-identical to the preceding attempt")
        if not isinstance(previous_workspace_tree_sha256, str) or SHA256_RE.fullmatch(previous_workspace_tree_sha256) is None:
            raise WorkflowError("previous workspace-tree hash is invalid")
        run_root = tasks_path.resolve().parent.parent
        sidecar_path = prior_evidence_path.with_name(prior_evidence_path.name + ".runner.json")
        previous_manifest_path = prior_evidence_path.parent / "candidate_manifest.jsonl"
        if not sidecar_path.is_file() or not previous_manifest_path.is_file():
            raise WorkflowError("preceding attempt sidecar or manifest is missing")

        binding = {
            "schema_version": REPAIR_HANDOFF_BINDING_SCHEMA_VERSION,
            "run_id": run_root.name,
            "task_id": task["task_id"],
            "source_id": task["source_id"],
            "top_module": task["top_module"],
            "attempt": repair_attempt,
            "candidate_id": candidate["candidate_id"],
            "candidate_sha256": candidate_hash,
            "previous_attempt": previous_attempt_number,
            "previous_candidate_id": previous_candidate_id,
            "previous_candidate_sha256": previous_candidate_hash,
            "previous_evidence_sha256": _sha256_file(prior_evidence_path),
            "previous_runner_sidecar_sha256": _sha256_file(sidecar_path),
            "previous_manifest_sha256": _sha256_file(previous_manifest_path),
            "previous_workspace_tree_sha256": previous_workspace_tree_sha256,
            "repair_packet_id": packet["packet_id"],
            "repair_packet_sha256": _sha256_file(repair_packet_path),
            "teacher_generation_binding_sha256": teacher_binding_hash,
            "packet_validation_report_sha256": packet_validation_hash,
            "qualification_binding_sha256": teacher_binding["qualification_binding_sha256"],
            "corrected_testbench_sha256": _sha256_bytes(testbench),
            "source_commit": teacher_binding["source_commit"],
            "source_tree_sha256": teacher_binding["source_tree_sha256"],
            "frozen_split_sha256": teacher_binding["frozen_split_sha256"],
            "correction_version": teacher_binding["correction_version"],
            "qualification_passed": True,
            "reference_rtl_supplied": False,
            "support_files": [],
        }
        _validate_repair_binding_object(binding, "repair binding")
        if output_path.exists() or output_path.is_symlink():
            raise WorkflowError(f"refusing to replace existing repair binding: {output_path}")
        _atomic_write(output_path, _json_bytes(binding, pretty=True))
        os.chmod(output_path, 0o600)
        return {"ok": True, "binding": _display_path(output_path), "errors": [], "warnings": []}, 0
    except (WorkflowError, OSError, UnicodeError, ValueError) as exc:
        return {"ok": False, "errors": [_report_error(exc)], "warnings": []}, 1


def _validate_plan(plan: Any, label: str) -> dict[str, Any]:
    if isinstance(plan, dict) and "qualification_binding" not in plan:
        plan = {**plan, "qualification_binding": None}
    if isinstance(plan, dict) and "teacher_generation_binding" not in plan:
        plan = {**plan, "teacher_generation_binding": None}
    if isinstance(plan, dict) and "repair_binding" not in plan:
        plan = {**plan, "repair_binding": None}
    _strict_fields(plan, PLAN_FIELDS, label)
    if plan["schema_version"] != PLAN_SCHEMA_VERSION or plan["verification_profile"] != PROFILE or plan["testbench_top"] != TESTBENCH_TOP or plan["simulation_result_contract"] != SIMULATION_CONTRACT:
        raise WorkflowError(f"{label} has an unsupported verification contract")
    if not isinstance(plan["candidate_id"], str) or not isinstance(plan["task_id"], str) or not isinstance(plan["source_id"], str):
        raise WorkflowError(f"{label} has invalid identity fields")
    if type(plan["attempt"]) is not int or not 1 <= plan["attempt"] <= 4:
        raise WorkflowError(f"{label}.attempt is invalid")
    if plan["candidate_id"] != f"{plan['task_id']}_attempt_{plan['attempt']:02d}":
        raise WorkflowError(f"{label}.candidate_id is not deterministic")
    if plan["qualification_binding"] is not None:
        _validate_qualification_binding(plan["qualification_binding"], f"{label}.qualification_binding")
    if plan["teacher_generation_binding"] is not None:
        _validate_teacher_generation_binding_object(
            plan["teacher_generation_binding"],
            f"{label}.teacher_generation_binding",
        )
    if plan["repair_binding"] is not None:
        _validate_repair_binding_object(plan["repair_binding"], f"{label}.repair_binding")
    _identifier(plan["top_module"], f"{label}.top_module")
    if plan["requested_checks"] != REQUESTED_CHECKS:
        raise WorkflowError(f"{label}.requested_checks is invalid")
    paths = plan["workspace_paths"]
    _strict_fields(paths, {"candidate_rtl_path", "testbench_path", "support_files"}, f"{label}.workspace_paths")
    _portable_relative(paths["candidate_rtl_path"], f"{label}.candidate path")
    _portable_relative(paths["testbench_path"], f"{label}.testbench path")
    if paths["candidate_rtl_path"] == paths["testbench_path"]:
        raise WorkflowError(f"{label}.candidate and testbench paths must be distinct")
    if not isinstance(paths["support_files"], list):
        raise WorkflowError(f"{label}.workspace_paths.support_files must be a list")
    for index, value in enumerate(paths["support_files"]):
        _portable_relative(value, f"{label}.support_files[{index}]")
    if len(set(paths["support_files"])) != len(paths["support_files"]):
        raise WorkflowError(f"{label}.support_files must be unique")
    hashes = plan["expected_hashes"]
    _strict_fields(hashes, {"candidate_rtl_sha256", "testbench_sha256", "support_files"}, f"{label}.expected_hashes")
    for field in ("candidate_rtl_sha256", "testbench_sha256"):
        if not isinstance(hashes[field], str) or not SHA256_RE.fullmatch(hashes[field]):
            raise WorkflowError(f"{label}.{field} is invalid")
    if not isinstance(hashes["support_files"], list) or len(hashes["support_files"]) != len(paths["support_files"]):
        raise WorkflowError(f"{label}.expected support hashes do not match paths")
    for index, item in enumerate(hashes["support_files"]):
        _strict_fields(item, {"path", "sha256"}, f"{label}.support_hash[{index}]")
        if (item["path"] != paths["support_files"][index]
                or not isinstance(item["sha256"], str)
                or not SHA256_RE.fullmatch(item["sha256"])):
            raise WorkflowError(f"{label}.support_hash[{index}] is invalid")
    return plan


def _validate_hashes(value: Any, plan: dict[str, Any], label: str) -> None:
    _strict_fields(value, {"candidate_rtl_sha256", "testbench_sha256", "support_files"}, label)
    expected = plan["expected_hashes"]
    if value != expected:
        raise WorkflowError(f"{label} does not match verification plan")


def _validate_toolchain(value: Any, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"iverilog", "vvp", "verilator", "yosys"}:
        raise WorkflowError(f"{label} must contain exactly the RTLBench tool names")
    for name, item in value.items():
        _strict_fields(item, {"available", "version"}, f"{label}.{name}")
        if type(item["available"]) is not bool or (item["version"] is not None and not isinstance(item["version"], str)):
            raise WorkflowError(f"{label}.{name} has invalid fields")


def _validate_checks(value: Any, requested: dict[str, bool]) -> dict[str, Any]:
    _strict_fields(value, {"compile", "simulation", "lint", "synthesis"}, "checks")
    expected = {"compile": "candidate", "simulation": "candidate_passes", "lint": "candidate", "synthesis": "candidate"}
    for check, leaf in expected.items():
        _strict_fields(value[check], {leaf}, f"checks.{check}")
        status = _status(value[check][leaf], f"checks.{check}.{leaf}")
        if requested[check]:
            if status["reason"] == "not_requested":
                raise WorkflowError(f"checks.{check}.{leaf} is required but marked not_requested")
            if not status["attempted"] and status["reason"] not in REQUIRED_UNATTEMPTED_REASONS:
                raise WorkflowError(f"checks.{check}.{leaf} has an unsupported unavailable reason")
        else:
            if status != {"attempted": False, "passed": None, "reason": "not_requested"}:
                raise WorkflowError(f"checks.{check}.{leaf} must be exactly not_requested")
    compile_status = value["compile"]["candidate"]
    simulation_status = value["simulation"]["candidate_passes"]
    if simulation_status["passed"] is True and compile_status["passed"] is not True:
        raise WorkflowError("simulation cannot pass when compile did not pass")
    return value


def _validate_mismatch(value: Any) -> dict[str, Any]:
    _strict_fields(value, {"contract", "reported_counts", "reported_sample_counts", "maximum_count", "timeout_reported"}, "mismatch_summary")
    if value["contract"] != SIMULATION_CONTRACT or not isinstance(value["reported_counts"], list) or not isinstance(value["reported_sample_counts"], list) or len(value["reported_counts"]) != len(value["reported_sample_counts"]):
        raise WorkflowError("mismatch_summary has an invalid contract or count list")
    if any(type(item) is not int or item < 0 for item in value["reported_counts"]):
        raise WorkflowError("mismatch counts must be non-negative integers")
    if any(item is not None and (type(item) is not int or item < 0) for item in value["reported_sample_counts"]):
        raise WorkflowError("mismatch sample counts are invalid")
    maximum = max(value["reported_counts"]) if value["reported_counts"] else None
    if ((value["maximum_count"] != maximum
            or (value["maximum_count"] is not None and type(value["maximum_count"]) is not int))
            or type(value["timeout_reported"]) is not bool):
        raise WorkflowError("mismatch_summary maximum or timeout flag is inconsistent")
    return value


def _build_source_line_index(plans: Iterable[dict[str, Any]], workspace_root: Path) -> tuple[set[bytes], bool]:
    paths: set[str] = set()
    for plan in plans:
        paths.add(plan["workspace_paths"]["candidate_rtl_path"])
        paths.add(plan["workspace_paths"]["testbench_path"])
        paths.update(plan["workspace_paths"]["support_files"])
    lines: set[bytes] = set()
    conservative = False
    for relative in sorted(paths):
        path = workspace_root / relative
        raw = _read_bounded(path, MAX_WORKSPACE_ARTIFACT_BYTES)
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(line) > MAX_SOURCE_INDEX_LINE_BYTES or len(lines) >= MAX_SOURCE_INDEX_LINES:
                conservative = True
                continue
            lines.add(line)
    return lines, conservative


def _diagnostic_leak(value: str, plan: dict[str, Any], source_index: tuple[set[bytes], bool] | None = None) -> bool:
    if "\x00" in value or len(value.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
        return True
    if _private_string_leak(value) or "workspace/" in value or ".rtlbench-run-" in value:
        return True
    for path in (plan["workspace_paths"]["candidate_rtl_path"], plan["workspace_paths"]["testbench_path"], *plan["workspace_paths"]["support_files"]):
        if path and path in value:
            return True
    if re.search(r"(?i)\b(?:authorization\s*:\s*bearer|bearer|api[_-]?key|token|password|secret)\b", value):
        return True
    if source_index is not None:
        lines, conservative = source_index
        encoded = value.encode("utf-8")
        if conservative or any(line and line in encoded for line in lines):
            return True
    return False


def _select_failure_category(checks: dict[str, Any], accepted: bool) -> str:
    if accepted:
        return "passed"
    required = [checks["compile"]["candidate"], checks["simulation"]["candidate_passes"]]
    for category in (
        "timeout",
        "compile_failure",
        "functional_mismatch",
        "simulation_result_missing",
        "simulation_failure",
        "tool_unavailable",
        "internal_error",
    ):
        if any(status.get("reason") == category for status in required):
            return category
    return "partial_failure"


def _evidence_row(evidence: Any, plan: dict[str, Any], source_index: tuple[set[bytes], bool] | None) -> dict[str, Any]:
    _strict_fields(evidence, EVIDENCE_FIELDS, "evidence row")
    for field in ("schema_version", "candidate_id", "task_id", "source_id", "top_module", "testbench_top", "simulation_result_contract"):
        if not isinstance(evidence[field], str):
            raise WorkflowError(f"evidence.{field} must be a string")
    if evidence["schema_version"] != EVIDENCE_SCHEMA_VERSION:
        raise WorkflowError("evidence has the wrong schema version")
    for field in ("candidate_id", "task_id", "source_id"):
        if evidence[field] != plan[field]:
            raise WorkflowError(f"evidence.{field} does not match plan")
    if evidence["attempt"] != plan["attempt"] or evidence["top_module"] != plan["top_module"] or evidence["testbench_top"] != TESTBENCH_TOP or evidence["simulation_result_contract"] != SIMULATION_CONTRACT:
        raise WorkflowError("evidence identity or contract does not match plan")
    if evidence["requested_checks"] != REQUESTED_CHECKS:
        raise WorkflowError("evidence requested_checks does not match plan")
    _validate_hashes(evidence["input_hashes"], plan, "evidence.input_hashes")
    _validate_toolchain(evidence["toolchain"], "evidence.toolchain")
    checks = _validate_checks(evidence["checks"], REQUESTED_CHECKS)
    mismatch = _validate_mismatch(evidence["mismatch_summary"])
    if not isinstance(evidence["accepted"], bool) or evidence["failure_category"] not in FAILURE_CATEGORIES:
        raise WorkflowError("evidence acceptance or failure category is invalid")
    diagnostics = evidence["diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > MAX_DIAGNOSTICS or any(not isinstance(item, str) for item in diagnostics):
        raise WorkflowError("evidence diagnostics are invalid")
    if len(set(diagnostics)) != len(diagnostics):
        raise WorkflowError("evidence diagnostics must be deterministic and deduplicated")
    if any(_diagnostic_leak(item, plan, source_index) for item in diagnostics):
        raise WorkflowError("evidence diagnostics contain private paths, source, or credentials")
    compile_status = checks["compile"]["candidate"]
    simulation_status = checks["simulation"]["candidate_passes"]
    accepted = compile_status["passed"] is True and simulation_status["passed"] is True
    if evidence["accepted"] != accepted:
        raise WorkflowError("evidence.accepted is inconsistent with required check leaves")
    if accepted:
        if (evidence["failure_category"] != "passed" or not mismatch["reported_counts"]
                or any(count != 0 for count in mismatch["reported_counts"])
                or mismatch["timeout_reported"]):
            raise WorkflowError("accepted mismatch_count_v1 evidence is inconsistent")
    else:
        if evidence["failure_category"] == "passed":
            raise WorkflowError("failed evidence cannot use failure_category=passed")
        expected_category = _select_failure_category(checks, accepted)
        if evidence["failure_category"] != expected_category:
            raise WorkflowError(f"failure_category is inconsistent; expected {expected_category}")
        positive_mismatch = any(count > 0 for count in mismatch["reported_counts"])
        if positive_mismatch and evidence["failure_category"] not in {"functional_mismatch", "timeout"}:
            raise WorkflowError("positive mismatch reports require functional_mismatch unless timeout has priority")
        timeout_reason = compile_status.get("reason") == "timeout" or simulation_status.get("reason") == "timeout"
        if mismatch["timeout_reported"] and not timeout_reason:
            raise WorkflowError("timeout marker requires a timeout leaf reason")
        if mismatch["timeout_reported"] and evidence["failure_category"] != "timeout":
            raise WorkflowError("timeout marker requires failure_category=timeout")
        if simulation_status.get("reason") == "simulation_result_missing" and (
                not simulation_status["attempted"] or compile_status["passed"] is not True):
            raise WorkflowError("simulation_result_missing requires an attempted simulation after compile passed")
    return evidence


def _load_plans(path: Path) -> list[dict[str, Any]]:
    plans = _load_jsonl(path, maximum=MAX_JSONL_BYTES)
    seen: set[str] = set()
    for index, plan in enumerate(plans, 1):
        _validate_plan(plan, f"plan row {index}")
        if plan["candidate_id"] in seen:
            raise WorkflowError("duplicate candidate_id in plan")
        seen.add(plan["candidate_id"])
    return plans


def ingest_candidate_evidence(
    plan_path: Path,
    evidence_path: Path,
    output_path: Path,
    *,
    append: bool = False,
    overwrite: bool = False,
) -> tuple[dict[str, Any], int]:
    try:
        if append and overwrite:
            raise WorkflowError("--append and --overwrite are mutually exclusive")
        if _contains_symlink(output_path) or output_path.is_symlink():
            raise WorkflowError("evidence output must not be a symlink")
        if output_path.exists() and (output_path.is_dir() or _is_hard_link(output_path)):
            raise WorkflowError("evidence output must be a regular non-hard-linked file")
        if _is_alias(output_path, plan_path) or _is_alias(output_path, evidence_path):
            raise WorkflowError("evidence output aliases an input")
        plans = _load_plans(plan_path)
        evidence_rows = _load_jsonl(evidence_path, maximum=MAX_JSONL_BYTES)
        if len(plans) != len(evidence_rows):
            raise WorkflowError("evidence row count does not match plan")
        workspace_root = plan_path.parent / "workspace"
        if not workspace_root.is_dir() or _contains_symlink(workspace_root):
            raise WorkflowError("plan workspace is missing or symlinked")
        for plan in plans:
            for relative, expected_hash in (
                (plan["workspace_paths"]["candidate_rtl_path"], plan["expected_hashes"]["candidate_rtl_sha256"]),
                (plan["workspace_paths"]["testbench_path"], plan["expected_hashes"]["testbench_sha256"]),
            ):
                path = workspace_root / relative
                if _contains_symlink(path) or not path.is_file() or _sha256_file(path, maximum=MAX_WORKSPACE_ARTIFACT_BYTES) != expected_hash:
                    raise WorkflowError(f"workspace hash mismatch or missing file: {relative}")
            for item in plan["expected_hashes"]["support_files"]:
                path = workspace_root / item["path"]
                if _contains_symlink(path) or not path.is_file() or _sha256_file(path, maximum=MAX_WORKSPACE_ARTIFACT_BYTES) != item["sha256"]:
                    raise WorkflowError(f"workspace support hash mismatch or missing file: {item['path']}")
        source_index = _build_source_line_index(plans, workspace_root)
        attempts: list[dict[str, Any]] = []
        accepted = 0
        for index, (plan, evidence) in enumerate(zip(plans, evidence_rows), 1):
            evidence = _evidence_row(evidence, plan, source_index)
            if evidence["accepted"]:
                accepted += 1
            attempts.append({
                "schema_version": ATTEMPT_SCHEMA_VERSION,
                "candidate_id": plan["candidate_id"], "task_id": plan["task_id"], "source_id": plan["source_id"],
                "attempt": plan["attempt"], "top_module": plan["top_module"],
                "candidate_sha256": plan["expected_hashes"]["candidate_rtl_sha256"],
                "verification_profile": PROFILE, "accepted": evidence["accepted"],
                "failure_category": evidence["failure_category"], "checks": evidence["checks"],
                "mismatch_summary": evidence["mismatch_summary"], "diagnostics": evidence["diagnostics"],
                "toolchain": evidence["toolchain"],
            })
        existing: list[dict[str, Any]] = []
        if output_path.exists():
            if not (append or overwrite):
                raise WorkflowError(f"output already exists: {output_path}; use --append or --overwrite")
            existing = _load_attempts(output_path)
            if overwrite:
                existing = []
        combined = existing + attempts
        _validate_attempt_history(combined)
        combined.sort(key=lambda row: (row["task_id"], row["attempt"], row["candidate_id"]))
        _atomic_write(output_path, b"".join(_json_bytes(row) for row in combined))
        accepted_total = sum(1 for row in combined if row["accepted"])
        return {"ok": True, "ingested_attempts": len(attempts), "total_attempts": len(combined), "accepted_attempts": accepted_total, "failed_attempts": len(combined) - accepted_total, "errors": [], "warnings": []}, 0
    except WorkflowError as exc:
        return {"ok": False, "ingested_attempts": 0, "errors": [_report_error(exc)], "warnings": []}, 1


def _validate_attempt(row: Any, label: str) -> dict[str, Any]:
    _strict_fields(row, ATTEMPT_FIELDS, label)
    if row["schema_version"] != ATTEMPT_SCHEMA_VERSION or row["verification_profile"] != PROFILE:
        raise WorkflowError(f"{label} has an invalid schema or profile")
    for field in ("candidate_id", "task_id", "source_id", "top_module", "candidate_sha256", "failure_category"):
        if not isinstance(row[field], str):
            raise WorkflowError(f"{label}.{field} is invalid")
    if type(row["attempt"]) is not int or not 1 <= row["attempt"] <= 4 or not isinstance(row["accepted"], bool):
        raise WorkflowError(f"{label} has invalid attempt or accepted")
    if not SHA256_RE.fullmatch(row["candidate_sha256"]) or row["failure_category"] not in FAILURE_CATEGORIES:
        raise WorkflowError(f"{label} has invalid hash or failure category")
    checks = _validate_checks(row["checks"], REQUESTED_CHECKS)
    _validate_mismatch(row["mismatch_summary"])
    accepted = checks["compile"]["candidate"]["passed"] is True and checks["simulation"]["candidate_passes"]["passed"] is True
    if row["accepted"] != accepted:
        raise WorkflowError(f"{label}.accepted is inconsistent with required checks")
    expected_category = _select_failure_category(checks, accepted)
    if row["failure_category"] != expected_category:
        raise WorkflowError(f"{label}.failure_category is inconsistent; expected {expected_category}")
    mismatch = row["mismatch_summary"]
    if accepted:
        if (not mismatch["reported_counts"] or not mismatch["reported_sample_counts"]
                or any(count != 0 for count in mismatch["reported_counts"])
                or mismatch["timeout_reported"]):
            raise WorkflowError(f"{label}.accepted mismatch summary is inconsistent")
    else:
        if any(count > 0 for count in mismatch["reported_counts"]) and row["failure_category"] not in {"functional_mismatch", "timeout"}:
            raise WorkflowError(f"{label} positive mismatch report has an inconsistent category")
        timeout_reason = any(checks[name][leaf].get("reason") == "timeout" for name, leaf in (("compile", "candidate"), ("simulation", "candidate_passes")))
        if mismatch["timeout_reported"] and not timeout_reason:
            raise WorkflowError(f"{label} timeout marker has no timeout leaf reason")
        if mismatch["timeout_reported"] and row["failure_category"] != "timeout":
            raise WorkflowError(f"{label} timeout category is inconsistent")
        simulation = checks["simulation"]["candidate_passes"]
        if simulation.get("reason") == "simulation_result_missing" and (not simulation["attempted"] or checks["compile"]["candidate"]["passed"] is not True):
            raise WorkflowError(f"{label} missing-result state is impossible")
    if (not isinstance(row["diagnostics"], list)
            or len(row["diagnostics"]) > MAX_DIAGNOSTICS
            or len(set(row["diagnostics"])) != len(row["diagnostics"])
            or any(not isinstance(item, str) or len(item.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES for item in row["diagnostics"])):
        raise WorkflowError(f"{label}.diagnostics is invalid")
    diagnostic_plan = {"workspace_paths": {"candidate_rtl_path": "", "testbench_path": "", "support_files": []}}
    if any(_diagnostic_leak(item, diagnostic_plan) for item in row["diagnostics"]):
        raise WorkflowError(f"{label}.diagnostics contain private paths or credentials")
    _validate_toolchain(row["toolchain"], f"{label}.toolchain")
    return row


def _load_attempts(path: Path) -> list[dict[str, Any]]:
    rows = _load_jsonl(path, maximum=MAX_JSONL_BYTES)
    for index, row in enumerate(rows, 1):
        _validate_attempt(row, f"attempt row {index}")
    return rows


def _validate_attempt_history(rows: list[dict[str, Any]]) -> None:
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, int]] = set()
    by_task: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(rows, 1):
        _validate_attempt(row, f"attempt row {index}")
        expected_id = f"{row['task_id']}_attempt_{row['attempt']:02d}"
        if row["candidate_id"] != expected_id:
            raise WorkflowError(f"attempt row {index} has a non-deterministic candidate_id")
        if row["candidate_id"] in seen_ids:
            raise WorkflowError(f"duplicate attempt candidate_id: {row['candidate_id']}")
        pair = (row["task_id"], row["attempt"])
        if pair in seen_pairs:
            raise WorkflowError(f"duplicate attempt record: {pair}")
        seen_ids.add(row["candidate_id"])
        seen_pairs.add(pair)
        by_task.setdefault(row["task_id"], []).append(row)
    for task_id, history in by_task.items():
        history.sort(key=lambda row: row["attempt"])
        if len({row["source_id"] for row in history}) != 1 or len({row["top_module"] for row in history}) != 1:
            raise WorkflowError(f"attempt history for {task_id} changes source or top module")
        expected = list(range(1, history[-1]["attempt"] + 1))
        actual = [row["attempt"] for row in history]
        if actual != expected:
            raise WorkflowError(f"attempt history for {task_id} must be contiguous from 1")
        accepted_positions = [index for index, row in enumerate(history) if row["accepted"]]
        if accepted_positions and accepted_positions[0] != len(history) - 1:
            raise WorkflowError(f"attempt history for {task_id} contains an attempt after acceptance")


def export_teacher_repair_packets(
    tasks_path: Path,
    candidates_path: Path,
    attempts_path: Path,
    output_dir: Path,
    *,
    max_attempts: int = 4,
    batch_size: int = 1,
    overwrite: bool = False,
) -> tuple[dict[str, Any], int]:
    try:
        if not 1 <= max_attempts <= 4 or batch_size < 1:
            raise WorkflowError("max-attempts must be in 1..4 and batch-size must be positive")
        tasks = _load_tasks(tasks_path)
        candidates = _load_candidate_records(candidates_path)
        attempts = _load_attempts(attempts_path)
        _validate_attempt_history(attempts)
        task_map = {task["task_id"]: task for task in tasks}
        candidate_map: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(candidates, 1):
            task = task_map.get(record["task_id"])
            if task is None:
                raise WorkflowError(f"candidate record {index} references an unknown task")
            _validate_candidate_object(record["candidate"], task, f"candidate record {index}.candidate")
            if record["source_id"] != task["source_id"] or record["candidate_id"] != f"{record['task_id']}_attempt_{record['attempt']:02d}":
                raise WorkflowError(f"candidate record {index} has inconsistent identity")
            candidate_map[record["candidate_id"]] = record
        by_candidate: dict[str, dict[str, Any]] = {}
        seen_attempts: set[tuple[str, int]] = set()
        for row in attempts:
            if row["candidate_id"] in by_candidate:
                raise WorkflowError(f"duplicate attempt candidate_id: {row['candidate_id']}")
            pair = (row["task_id"], row["attempt"])
            if pair in seen_attempts:
                raise WorkflowError(f"duplicate attempt record: {pair}")
            seen_attempts.add(pair)
            task = task_map.get(row["task_id"])
            if task is None:
                raise WorkflowError(f"attempt references an unknown task: {row['task_id']}")
            if row["source_id"] != task["source_id"] or row["top_module"] != task["top_module"]:
                raise WorkflowError(f"attempt task identity mismatch: {row['task_id']}")
            candidate = candidate_map.get(row["candidate_id"])
            if candidate is None:
                raise WorkflowError(f"missing candidate for attempt {row['candidate_id']}")
            if candidate["task_id"] != row["task_id"] or candidate["source_id"] != row["source_id"] or candidate["attempt"] != row["attempt"]:
                raise WorkflowError(f"candidate/attempt identity mismatch: {row['candidate_id']}")
            if candidate["candidate_sha256"] != row["candidate_sha256"]:
                raise WorkflowError(f"candidate/attempt hash mismatch: {row['candidate_id']}")
            by_candidate[row["candidate_id"]] = row
        by_task_candidate: dict[tuple[str, int], dict[str, Any]] = {}
        for row in candidates:
            key = (row["task_id"], row["attempt"])
            by_task_candidate[key] = row
        task_order = {task["task_id"]: index for index, task in enumerate(tasks)}
        selected: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for task in tasks:
            all_task_attempts = [row for row in attempts if row["task_id"] == task["task_id"]]
            all_task_attempts.sort(key=lambda row: row["attempt"])
            if all_task_attempts and all_task_attempts[-1]["accepted"]:
                continue
            failed = [row for row in all_task_attempts if not row["accepted"]]
            if not failed:
                continue
            latest = max(failed, key=lambda row: row["attempt"])
            if latest["attempt"] >= max_attempts:
                continue
            if latest["failure_category"] not in REPAIRABLE_FAILURE_CATEGORIES:
                continue
            candidate = by_task_candidate.get((task["task_id"], latest["attempt"]))
            if candidate is None:
                raise WorkflowError(f"missing candidate record for failed attempt {latest['candidate_id']}")
            if candidate["candidate_id"] != latest["candidate_id"]:
                raise WorkflowError(f"attempt/candidate identity mismatch: {task['task_id']}")
            selected.append((task, candidate, latest))
        if not selected:
            return {"ok": True, "repairable_attempts": 0, "packet_count": 0, "packets": [], "errors": [], "warnings": []}, 0
        # Packet target_attempt is singular, so keep mixed retry numbers in
        # separate deterministic groups while preserving task input order.
        groups: list[tuple[int, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]]] = []
        for target in sorted({row[2]["attempt"] + 1 for row in selected}):
            groups.append((target, [row for row in selected if row[2]["attempt"] + 1 == target]))
        packet_count = sum((len(rows) + batch_size - 1) // batch_size for _, rows in groups)
        names = [f"packet_{index:04d}.{suffix}" for index in range(1, packet_count + 1) for suffix in ("json", "md")]
        _prepare_output_files(output_dir, names, overwrite)
        json_paths: list[str] = []
        md_paths: list[str] = []
        packet_number = 0
        for target, group in groups:
            for offset in range(0, len(group), batch_size):
                packet_number += 1
                batch = group[offset:offset + batch_size]
                rows = []
                for task, candidate, attempt in batch:
                    checks = attempt["checks"]
                    compile_reason = checks["compile"]["candidate"].get("reason")
                    simulation_reason = checks["simulation"]["candidate_passes"].get("reason")
                    feedback = {
                        "failure_category": attempt["failure_category"],
                        "compile_reason": compile_reason,
                        "simulation_reason": simulation_reason,
                        "mismatch_summary": attempt["mismatch_summary"],
                        "diagnostics": [item for item in attempt["diagnostics"] if not _diagnostic_leak(item, {"workspace_paths": {"candidate_rtl_path": "", "testbench_path": "", "support_files": []}})],
                    }
                    rows.append({"task": task, "previous_candidate": candidate["candidate"], "verification_feedback": feedback})
                packet = {
                    "schema_version": REPAIR_PACKET_SCHEMA_VERSION,
                    "packet_id": _packet_id("repair", packet_number, [row["task"]["task_id"] for row in rows], target, PROMPT_REPAIR_VERSION),
                    "packet_kind": "repair", "target_attempt": target, "row_count": len(rows), "rows": rows,
                }
                if any(key.lower() in PRIVATE_KEY_NAMES for key in _walk_keys(packet)):
                    raise WorkflowError("repair packet contains a private field")
                markdown = _repair_markdown(packet)
                path, md = _write_packet_batch(output_dir, packet_number, packet, markdown, overwrite)
                json_paths.append(path); md_paths.append(md)
        return {"ok": True, "repairable_attempts": len(selected), "packet_count": packet_count, "packets": [_display_path(Path(path)) for path in json_paths], "packet_markdown": [_display_path(Path(path)) for path in md_paths], "errors": [], "warnings": []}, 0
    except WorkflowError as exc:
        return {"ok": False, "repairable_attempts": 0, "errors": [_report_error(exc)], "warnings": []}, 1


# Short aliases make the reusable module convenient for tests and callers.
export_rtl_teacher_generation_packets = export_teacher_generation_packets
validate_rtl_teacher_candidate_batch = validate_teacher_candidate_batch
validate_rtl_teacher_candidate_response_set = validate_teacher_candidate_response_set
prepare_rtl_candidate_verification = prepare_candidate_verification
ingest_rtl_candidate_evidence = ingest_candidate_evidence
export_rtl_teacher_repair_packets = export_teacher_repair_packets
create_rtl_teacher_repair_binding = create_teacher_repair_binding
