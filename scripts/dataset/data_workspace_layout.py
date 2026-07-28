"""Safe, deterministic Data Workspace Layout v2 helpers.

This module deliberately has no subprocess, model, RTL, or EDA integration.  It
only inspects ordinary filesystem metadata/bytes and manages the local layout
contracts described by ``data-workspace-layout-v2``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Iterable


LAYOUT_VERSION = "data_workspace_v2"
INVENTORY_SCHEMA_VERSION = "data_workspace_inventory_v0.1"
MIGRATION_SCHEMA_VERSION = "data_workspace_migration_plan_v0.1"
RUN_MANIFEST_SCHEMA_VERSION = "manual_rtl_run_manifest_v0.1"
RUN_WORKFLOW = "manual_rtl_teacher"
RUN_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

CATEGORIES = (
    "reviewed_seed",
    "raw_source",
    "normalized_task",
    "teacher_answer",
    "workflow_public",
    "workflow_private",
    "verification",
    "human_review",
    "distill_package",
    "evaluation",
    "report",
    "archive",
    "legacy",
    "unknown",
)
SENSITIVITIES = ("committed_public", "local_public", "private_local", "unknown")

CANONICAL_RUN_PATHS = {
    "normalization_packets": "normalization/packets",
    "normalization_responses": "normalization/responses",
    "generation_tasks": "tasks/generation_tasks.jsonl",
    "verification_assets": "tasks/verification_assets.jsonl",
    "private_assets": "private_assets",
    "teacher_packets": "teacher/packets",
    "teacher_responses": "teacher/responses",
    "candidate_records": "teacher/candidate_records.jsonl",
    "verification": "verification",
    "generation_attempts": "verification/generation_attempts.jsonl",
    "repairs": "repairs",
    "review": "review",
    "reports": "reports",
}
REQUIRED_RUN_DIRECTORIES = (
    "normalization/packets",
    "normalization/responses",
    "tasks",
    "private_assets",
    "teacher/packets",
    "teacher/responses",
    "verification",
    "repairs",
    "review",
    "reports",
)
MANIFEST_FIELDS = {"schema_version", "layout_version", "run_id", "workflow", "source_dataset", "max_attempts", "paths"}


class WorkspaceError(ValueError):
    """A safe, deterministic workspace preflight or validation failure."""


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WorkspaceError(f"could not hash regular file {path.name}: {exc}") from exc
    return digest.hexdigest()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _contains_symlink(path: Path) -> bool:
    """Check lexical ancestors without resolving or following a symlink."""

    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError as exc:
            raise WorkspaceError(f"could not inspect path {current.name}: {exc}") from exc
    return False


def _reject_symlink_tree(root: Path, label: str) -> None:
    if root.is_symlink() or _contains_symlink(root):
        raise WorkspaceError(f"{label} contains a symlink")
    if not root.exists():
        return
    if not root.is_dir():
        return
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise WorkspaceError(f"could not inspect {label}: {exc}") from exc
        for child in children:
            if child.is_symlink():
                raise WorkspaceError(f"{label} contains a symlink: {child.name}")
            if child.is_dir():
                pending.append(child)


def _ensure_directory(path: Path, label: str) -> Path:
    requested = _absolute(path)
    if _contains_symlink(requested):
        raise WorkspaceError(f"{label} path contains a symlink")
    if requested.exists():
        if not requested.is_dir():
            raise WorkspaceError(f"{label} is not a directory")
        return requested
    missing: list[Path] = []
    current = requested
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            raise WorkspaceError(f"could not find an existing ancestor for {label}")
        current = current.parent
    if _contains_symlink(current) or not current.is_dir():
        raise WorkspaceError(f"{label} existing ancestor is unsafe")
    for directory in reversed(missing):
        try:
            directory.mkdir()
            os.chmod(directory, 0o755)
        except FileExistsError:
            if _contains_symlink(directory) or not directory.is_dir():
                raise WorkspaceError(f"{label} became unsafe while creating")
        except OSError as exc:
            raise WorkspaceError(f"could not create {label}: {exc}") from exc
    return requested


def _missing_directory_ancestors(path: Path) -> list[Path]:
    requested = _absolute(path)
    missing: list[Path] = []
    current = requested
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return missing


def _dangerous_root(path: Path) -> bool:
    resolved = _absolute(path).resolve()
    candidates = {
        Path(resolved.anchor),
        Path.home().resolve(),
        Path.cwd().resolve(),
        Path(__file__).resolve().parents[2],
    }
    return resolved in candidates


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WorkspaceError(f"{label} must be a non-empty relative POSIX path")
    if value.startswith("/") or WINDOWS_DRIVE_RE.match(value) or "\\" in value:
        raise WorkspaceError(f"{label} must be a normalized POSIX-relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise WorkspaceError(f"{label} contains an unsafe path component")
    normalized = Path(*parts).as_posix()
    if normalized != value:
        raise WorkspaceError(f"{label} is not normalized")
    return value


def _root_label(data_root: Path) -> str:
    return data_root.name or "data"


def _display_path(data_root: Path, path: Path) -> str:
    relative = _absolute(path).relative_to(_absolute(data_root)).as_posix()
    root_name = _root_label(data_root)
    return f"{root_name}/{relative}" if relative else root_name


def _internal_path(data_root: Path, display_path: str) -> Path:
    prefix = _root_label(data_root) + "/"
    if display_path == _root_label(data_root):
        return data_root
    if not display_path.startswith(prefix):
        raise WorkspaceError(f"path is outside the data root: {display_path}")
    relative = _safe_relative(display_path[len(prefix):], "workspace path")
    return data_root.joinpath(*relative.split("/"))


def _safe_output_path(path: Path, *, force: bool) -> Path:
    absolute = _absolute(path)
    if _contains_symlink(absolute):
        raise WorkspaceError("output path contains a symlink")
    if absolute.exists():
        if absolute.is_symlink() or absolute.is_dir():
            raise WorkspaceError("output must be a regular file")
        try:
            if absolute.stat().st_nlink > 1:
                raise WorkspaceError("output must not be a hard-linked file")
        except OSError as exc:
            raise WorkspaceError(f"could not inspect output: {exc}") from exc
        if not force:
            raise WorkspaceError("output already exists; use --force")
    _ensure_directory(absolute.parent, "output parent")
    return absolute


def atomic_write_json(path: Path, payload: dict[str, Any], *, force: bool = False) -> None:
    destination = _safe_output_path(path, force=force)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".data-workspace-", dir=os.fspath(destination.parent))
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(payload, pretty=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _classify(relative: str) -> tuple[str, str, bool, str | None, str]:
    """Return category, sensitivity, legacy, recommendation, and reason."""

    parts = relative.split("/") if relative else []
    lowered = relative.casefold()
    legacy = ".local_data" in parts
    recommendation: str | None = None
    reason = "path is outside a canonical Data Workspace v2 product or workflow"

    if relative == "README.md":
        return "workflow_public", "committed_public", False, None, "committed data-workspace guidance"
    if relative.endswith("/.gitkeep") or relative == ".gitkeep":
        marker_category = {
            "golden": "reviewed_seed",
            "raw": "raw_source",
            "normalized": "normalized_task",
            "answers": "teacher_answer",
            "runs": "workflow_public",
            "review": "human_review",
            "distill": "distill_package",
            "eval": "evaluation",
            "reports": "report",
            "archive": "archive",
        }.get(parts[0] if parts else "", "workflow_public")
        return marker_category, "committed_public", False, None, "committed empty-workspace marker"

    if parts and parts[0] == "golden":
        return "reviewed_seed", "committed_public", False, None, "existing intentionally reviewed seed dataset"
    if parts and parts[0] == "distill":
        return "distill_package", "private_local", False, None, "final packaged train, validation, or test distillation dataset"
    if parts and parts[0] == "eval":
        return "evaluation", "private_local", False, None, "evaluation prompts, predictions, runs, or comparisons"
    if parts and parts[0] == "reports":
        return "report", "private_local", False, None, "inventory, migration, validation, or workflow report"
    if parts and parts[0] == "archive":
        return "archive", "private_local", False, None, "superseded local output retained for traceability"
    if parts and parts[0] == "normalized":
        return "normalized_task", "private_local", False, None, "reusable canonical task representation"
    if parts and parts[0] == "answers":
        return "teacher_answer", "private_local", False, None, "row-level teacher-answer workflow state"
    if parts and parts[0] == "raw":
        sensitivity = "private_local" if len(parts) > 1 and parts[1] == "internal" else "local_public"
        return "raw_source", sensitivity, False, None, "immutable upstream source material"
    if parts and parts[0] == "runs":
        if "private_assets" in parts:
            return "workflow_private", "private_local", False, None, "private verification assets for one local workflow run"
        if "verification" in parts:
            return "verification", "private_local", False, None, "attempt-specific verification state"
        return "workflow_public", "local_public", False, None, "untrusted state for a specific workflow run"
    if parts and parts[0] == "review":
        canonical_review = len(parts) > 1 and parts[1] == "manual_rtl_teacher"
        if any(marker in lowered for marker in ("rtl_generation_normalization_batches", "rtl_teacher_generation_packets", "rtl_teacher_repair_packets")):
            category = "workflow_public"
            reason = "legacy unreviewed teacher workflow packet state"
        else:
            category = "human_review"
            reason = "review collection or review decision state awaiting explicit promotion"
        return category, "private_local", not canonical_review, None, reason
    if parts and parts[0] == ".local_data":
        legacy = True
        if "rtl_generation_verification_assets" in lowered or "rtl_candidate_verification" in lowered or "manual_teacher_responses" in lowered:
            category = "verification" if "verification" in lowered else "workflow_private"
            reason = "legacy private RTL workflow state"
        else:
            category = "raw_source"
            reason = "legacy local source workspace"
        if "verilog-eval-main/dataset_spec-to-rtl" in lowered:
            recommendation = "data/raw/verilog_eval/upstream/dataset_spec-to-rtl"
        return category, "private_local", legacy, recommendation, reason
    if parts and parts[0] == "raw_public":
        return "legacy", "local_public", True, "data/raw", "legacy raw-public workspace; use data/raw/<dataset>/"
    if parts and parts[0] in {"drafts", "processed", "heldout"}:
        return "legacy", "private_local", True, None, "legacy dataset workflow workspace"

    if lowered.startswith(".local_data/verilog-eval-main/"):
        return "raw_source", "private_local", True, recommendation, "legacy VerilogEval source checkout"
    return "unknown", "unknown", legacy, recommendation, reason


def _recommended_for_legacy(relative: str) -> str | None:
    if relative.startswith(".local_data/verilog-eval-main/"):
        name = relative.rsplit("/", 1)[-1]
        if name.casefold().startswith("license") or name.casefold().startswith("readme"):
            return f"data/raw/verilog_eval/upstream/{name}"
    mappings = (
        (".local_data/verilog-eval-main/dataset_spec-to-rtl", "data/raw/verilog_eval/upstream/dataset_spec-to-rtl"),
        (".local_data/manual_task_normalization", "data/runs/manual_rtl_teacher/{run-id}/normalization/responses"),
        (".local_data/rtl_generation_verification_assets", "data/runs/manual_rtl_teacher/{run-id}/private_assets"),
        (".local_data/manual_teacher_responses", "data/runs/manual_rtl_teacher/{run-id}/teacher/responses"),
        (".local_data/rtl_candidate_verification", "data/runs/manual_rtl_teacher/{run-id}/verification"),
        ("review/rtl_generation_normalization_batches", "data/runs/manual_rtl_teacher/{run-id}/normalization/packets"),
        ("review/rtl_generation_pilot", "data/runs/manual_rtl_teacher/{run-id}"),
        ("review/rtl_teacher_generation_packets", "data/runs/manual_rtl_teacher/{run-id}/teacher/packets"),
        ("review/rtl_teacher_repair_packets", "data/runs/manual_rtl_teacher/{run-id}/repairs"),
    )
    for source, destination in mappings:
        if relative == source or relative.startswith(source + "/"):
            suffix = relative[len(source):].lstrip("/")
            return destination if not suffix else f"{destination}/{suffix}"
    return None


@dataclass
class _ScannedEntry:
    relative: str
    entry_type: str
    size_bytes: int
    file_count: int
    sha256: str | None
    hard_link_count: int = 0
    files: tuple[tuple[str, int, str], ...] = ()


def _directory_digest(files: Iterable[tuple[str, int, str]]) -> str:
    rows = sorted(files, key=lambda item: item[0])
    encoded = b"".join(_json_bytes({"path": path, "size_bytes": size, "sha256": digest}) for path, size, digest in rows)
    return _sha256_bytes(encoded)


def _scan_tree(data_root: Path, excluded: set[Path] | None = None) -> list[_ScannedEntry]:
    if data_root.is_symlink() or _contains_symlink(data_root):
        raise WorkspaceError("data root must not be a symlink or have symlinked ancestors")
    if not data_root.exists() or not data_root.is_dir():
        raise WorkspaceError("data root must be an existing directory")

    entries: list[_ScannedEntry] = []
    excluded = excluded or set()

    def visit(directory: Path, relative: str) -> tuple[int, int, tuple[tuple[str, int, str], ...]]:
        total_size = 0
        file_count = 0
        all_files: list[tuple[str, int, str]] = []
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise WorkspaceError(f"could not inspect data tree: {exc}") from exc
        for child in children:
            if _absolute(child) in excluded:
                continue
            child_relative = f"{relative}/{child.name}" if relative else child.name
            if child.is_symlink():
                raise WorkspaceError(f"data tree contains a symlink: {child_relative}")
            try:
                mode = child.lstat().st_mode
            except OSError as exc:
                raise WorkspaceError(f"could not inspect data entry {child_relative}: {exc}") from exc
            if stat.S_ISDIR(mode):
                size, count, files = visit(child, child_relative)
                total_size += size
                file_count += count
                all_files.extend((f"{child.name}/{path}", item_size, digest) for path, item_size, digest in files)
                child_entry = next(item for item in entries if item.relative == child_relative)
                child_entry.files = files
                continue
            if stat.S_ISREG(mode):
                size = child.stat().st_size
                digest = _sha256_file(child)
                links = child.stat().st_nlink
                entry = _ScannedEntry(child_relative, "file", size, 1, digest, links if links > 1 else 0, ((child.name, size, digest),))
                entries.append(entry)
                total_size += size
                file_count += 1
                all_files.append((child.name, size, digest))
                continue
            size = child.lstat().st_size
            entries.append(_ScannedEntry(child_relative, "special", size, 0, None))
        digest = _directory_digest(all_files)
        if relative:
            entries.append(_ScannedEntry(relative, "directory", total_size, file_count, digest, files=tuple(all_files)))
        return total_size, file_count, tuple(all_files)

    visit(data_root, "")
    entries.sort(key=lambda item: item.relative)
    return entries


def build_inventory(data_root: Path, *, exclude_paths: Iterable[Path] = ()) -> dict[str, Any]:
    root = _absolute(data_root)
    excluded = {_absolute(path) for path in exclude_paths if _is_relative_to(_absolute(path), root)}
    scanned = _scan_tree(root, excluded)
    display_entries: list[dict[str, Any]] = []
    unknown_paths: list[str] = []
    for item in scanned:
        category, sensitivity, legacy, recommendation, reason = _classify(item.relative)
        recommendation = _recommended_for_legacy(item.relative) or recommendation
        path = _display_path(root, root / item.relative) if item.relative else _root_label(root)
        entry = {
            "path": path,
            "entry_type": item.entry_type,
            "category": category,
            "sensitivity": sensitivity,
            "legacy": legacy,
            "size_bytes": item.size_bytes,
            "file_count": item.file_count,
            "sha256": item.sha256,
            "recommended_path": recommendation,
            "reason": reason,
        }
        if item.entry_type == "file":
            entry["hard_link_count"] = item.hard_link_count
        display_entries.append(entry)
        if category == "unknown":
            unknown_paths.append(path)
    summary = {
        "entry_count": len(display_entries),
        "file_count": sum(1 for item in scanned if item.entry_type == "file"),
        "directory_count": sum(1 for item in scanned if item.entry_type == "directory"),
        "total_bytes": sum(item.size_bytes for item in scanned if item.entry_type == "file"),
        "raw_source_bytes": sum(item.size_bytes for item in scanned if item.entry_type == "file" and _classify(item.relative)[0] == "raw_source"),
        "legacy_entry_count": sum(1 for item in display_entries if item["legacy"]),
        "unknown_entry_count": sum(1 for item in display_entries if item["category"] == "unknown"),
        "hard_link_file_count": sum(1 for item in scanned if item.entry_type == "file" and item.hard_link_count),
    }
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "layout_version": LAYOUT_VERSION,
        "data_root": _root_label(root),
        "entries": display_entries,
        "summary": summary,
        "unknown_paths": sorted(unknown_paths),
    }


def inventory_data_workspace(data_root: Path, output: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    root = _absolute(data_root)
    output_abs = _absolute(output) if output is not None else None
    if output_abs is not None:
        _safe_output_path(output_abs, force=force)
    report = build_inventory(root, exclude_paths=(output_abs,) if output_abs is not None else ())
    if output is not None:
        assert output_abs is not None
        atomic_write_json(output_abs, report, force=force)
    return report


def _manifest(run_id: str, source_dataset: str) -> dict[str, Any]:
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "layout_version": LAYOUT_VERSION,
        "run_id": run_id,
        "workflow": RUN_WORKFLOW,
        "source_dataset": source_dataset,
        "max_attempts": 4,
        "paths": dict(CANONICAL_RUN_PATHS),
    }


def _validate_manifest(value: Any, *, expected_run_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != MANIFEST_FIELDS:
        raise WorkspaceError("run manifest has unknown or missing fields")
    if value["schema_version"] != RUN_MANIFEST_SCHEMA_VERSION or value["layout_version"] != LAYOUT_VERSION:
        raise WorkspaceError("run manifest has an unsupported schema or layout version")
    run_id = value["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise WorkspaceError("run manifest has an invalid run_id")
    if expected_run_id is not None and run_id != expected_run_id:
        raise WorkspaceError("run manifest run_id does not match the run directory")
    if value["workflow"] != RUN_WORKFLOW or not isinstance(value["source_dataset"], str) or not value["source_dataset"].strip() or "/" in value["source_dataset"] or "\\" in value["source_dataset"]:
        raise WorkspaceError("run manifest has an invalid workflow or source_dataset")
    if value["max_attempts"] != 4 or type(value["max_attempts"]) is not int:
        raise WorkspaceError("manual RTL runs require max_attempts exactly 4")
    paths = value["paths"]
    if not isinstance(paths, dict) or set(paths) != set(CANONICAL_RUN_PATHS):
        raise WorkspaceError("run manifest path map is not canonical")
    for key, expected in CANONICAL_RUN_PATHS.items():
        if _safe_relative(paths[key], f"manifest.paths.{key}") != expected:
            raise WorkspaceError(f"manifest.paths.{key} is not canonical")
    return value


def _read_json_file(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise WorkspaceError(f"{label} must be a regular file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"{label} is not valid JSON") from exc


def _walk_no_symlink(root: Path) -> list[Path]:
    paths: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise WorkspaceError(f"could not inspect run tree: {exc}") from exc
        for child in children:
            if child.is_symlink():
                raise WorkspaceError(f"run tree contains a symlink: {child.name}")
            paths.append(child)
            if child.is_dir():
                pending.append(child)
    return paths


def initialize_manual_rtl_run(run_id: str, source_dataset: str, runs_root: Path, *, resume: bool = False) -> dict[str, Any]:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise WorkspaceError("run-id must match ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    root = _absolute(runs_root)
    if _dangerous_root(root):
        raise WorkspaceError("refusing a dangerous runs root")
    if _contains_symlink(root):
        raise WorkspaceError("runs root has a symlinked ancestor")
    _ensure_directory(root, "runs root")
    run_root = root / run_id
    if run_root.exists() or run_root.is_symlink():
        if not resume:
            raise WorkspaceError("run already exists; use --resume only for an exact existing run")
        report, code = validate_manual_rtl_run(run_root)
        if code:
            raise WorkspaceError("existing run does not match the exact manifest and structure")
        if report["manifest"]["source_dataset"] != source_dataset:
            raise WorkspaceError("existing run source_dataset does not match requested source_dataset")
        return {"ok": True, "status": "resumed", "run_id": run_id, "run_root": run_id, "manifest": report["manifest"]}

    manifest = _manifest(run_id, source_dataset)
    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=os.fspath(root)))
        os.chmod(stage, 0o755)
        for relative in REQUIRED_RUN_DIRECTORIES:
            directory = stage.joinpath(*relative.split("/"))
            directory.mkdir(parents=True, exist_ok=False)
            os.chmod(directory, 0o755)
        manifest_path = stage / "run_manifest.json"
        manifest_path.write_bytes(_json_bytes(manifest, pretty=True))
        os.chmod(manifest_path, 0o644)
        if run_root.exists() or run_root.is_symlink():
            raise WorkspaceError("run appeared during atomic publication")
        os.replace(stage, run_root)
        stage = None
    finally:
        if stage is not None:
            _remove_tree(stage)
    return {"ok": True, "status": "created", "run_id": run_id, "run_root": run_id, "manifest": manifest}


def _relative_run_path(run_root: Path, path: Path) -> str:
    return path.relative_to(run_root).as_posix()


def _text_contains_forbidden_claim(text: str) -> bool:
    lowered = text.casefold()
    if ".local_data" in lowered or WINDOWS_DRIVE_RE.search(text):
        return True
    if re.search(r"(?<![A-Za-z0-9_])/(?:home|root|tmp|var|etc)/", text):
        return True
    return False


def _contains_approval_claim(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in {"approved", "reviewed", "training_ready", "promoted", "packaged", "approval_granted"}:
                if child is True or (isinstance(child, str) and child.casefold() in {"true", "yes", "approved", "accepted"}):
                    return True
            if _contains_approval_claim(child):
                return True
    elif isinstance(value, list):
        return any(_contains_approval_claim(item) for item in value)
    return False


def _teacher_visible_checks(run_root: Path, paths: list[Path]) -> list[str]:
    errors: list[str] = []
    roots = (run_root / "normalization", run_root / "teacher" / "packets", run_root / "repairs", run_root / "review")
    for path in paths:
        if not path.is_file() or not any(_is_relative_to(path, root) for root in roots if root.exists()):
            continue
        relative = _relative_run_path(run_root, path)
        lowered_name = path.name.casefold()
        if path.suffix.casefold() in {".sv", ".v", ".svh", ".vh"} or any(marker in lowered_name for marker in ("reference", "testbench", "support", "_ref.")):
            errors.append(f"teacher-visible path contains private RTL/testbench/support material: {relative}")
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"could not read teacher-visible file {relative}: {exc}")
            continue
        if _text_contains_forbidden_claim(text):
            errors.append(f"teacher-visible file contains a private/local path: {relative}")
        if re.search(r"\b(?:approved|approval|training[-_ ]ready|promoted|packaged)\b", text, re.IGNORECASE):
            errors.append(f"teacher-visible file contains an approval/package claim: {relative}")
        if re.search(r'"(?:reference_rtl|raw_reference_rtl|testbench|testbench_text|support_files|expected_vectors|expected_outputs|benchmark_answers)"\s*:', text):
            errors.append(f"teacher-visible file contains private artifact fields: {relative}")
        if _contains_approval_claim(_try_json(raw)):
            errors.append(f"teacher-visible file contains an approval claim: {relative}")
    return errors


def _try_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def validate_manual_rtl_run(run_root: Path) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    root = _absolute(run_root)
    try:
        if root.is_symlink() or _contains_symlink(root) or not root.is_dir():
            raise WorkspaceError("run root must be a real directory without symlinked ancestors")
        paths = _walk_no_symlink(root)
        top_names = {path.name for path in root.iterdir()}
        expected_top = {"run_manifest.json", "normalization", "tasks", "private_assets", "teacher", "verification", "repairs", "review", "reports"}
        unexpected = sorted(top_names - expected_top)
        if unexpected:
            errors.append(f"unexpected top-level run entries: {unexpected}")
        manifest_path = root / "run_manifest.json"
        manifest = _validate_manifest(_read_json_file(manifest_path, "run_manifest"), expected_run_id=root.name)
        for relative in REQUIRED_RUN_DIRECTORIES:
            directory = root.joinpath(*relative.split("/"))
            if not directory.is_dir() or directory.is_symlink():
                errors.append(f"missing required directory: {relative}")

        allowed_files = {
            "run_manifest.json",
            "tasks/generation_tasks.jsonl",
            "tasks/verification_assets.jsonl",
            "teacher/candidate_records.jsonl",
            "verification/generation_attempts.jsonl",
            "review/queue.jsonl",
            "review/decisions.jsonl",
            "review/summary.json",
        }
        for path in paths:
            relative = _relative_run_path(root, path)
            if path.is_file():
                if relative in {"run_manifest.json", "tasks/generation_tasks.jsonl", "tasks/verification_assets.jsonl", "teacher/candidate_records.jsonl", "verification/generation_attempts.jsonl", "review/queue.jsonl", "review/decisions.jsonl", "review/summary.json"}:
                    continue
                if relative.endswith("/candidate_records.jsonl") or relative.endswith("/generation_attempts.jsonl"):
                    errors.append(f"workflow record is in the wrong folder: {relative}")
                    continue
                if relative.startswith("normalization/packets/") or relative.startswith("normalization/responses/") or relative.startswith("teacher/packets/") or relative.startswith("teacher/responses/") or relative in {"private_assets/verification_assets.jsonl"} or relative.startswith("private_assets/workspace/") or relative.startswith("reports/"):
                    continue
                if re.match(r"verification/attempt_0[1-4]/", relative) or re.match(r"repairs/attempt_0[2-4]/", relative):
                    continue
                errors.append(f"unexpected file in run: {relative}")
            elif path.is_dir():
                if relative.startswith("verification/attempt_"):
                    if not re.fullmatch(r"verification/attempt_0[1-4]", relative):
                        errors.append(f"invalid verification attempt directory: {relative}")
                elif relative.startswith("repairs/attempt_"):
                    if not re.fullmatch(r"repairs/attempt_0[2-4]", relative):
                        errors.append(f"invalid repair attempt directory: {relative}")
                elif relative.count("/") == 0 or relative in {"normalization", "teacher"} or relative in {"normalization/packets", "normalization/responses", "tasks", "private_assets", "private_assets/workspace", "teacher/packets", "teacher/responses", "verification", "repairs", "review", "reports"}:
                    continue
                elif not (relative.startswith("private_assets/workspace/") or relative.startswith("reports/") or re.fullmatch(r"verification/attempt_0[1-4]/.*", relative) or re.fullmatch(r"repairs/attempt_0[2-4]/.*", relative)):
                    errors.append(f"unexpected directory in run: {relative}")
        errors.extend(_teacher_visible_checks(root, paths))
        # The manifest itself is also a privacy boundary even though it is not
        # teacher-visible packet content.
        if _text_contains_forbidden_claim((root / "run_manifest.json").read_bytes().decode("utf-8", errors="replace")):
            errors.append("run manifest contains a private/local path")
    except WorkspaceError as exc:
        errors.append(str(exc))
        manifest = None
    report = {
        "ok": not errors,
        "run_root": root.name,
        "manifest": manifest,
        "required_directories": list(REQUIRED_RUN_DIRECTORIES),
        "errors": sorted(set(errors)),
        "warnings": [],
    }
    return report, 0 if not errors else 1


def _forbidden_source_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in {".git", "__pycache__", ".pytest_cache", ".cache", ".env", "credentials", "credentials.json"}
        or lowered.startswith(".#")
        or lowered.endswith(("~", ".swp", ".swo", ".tmp", ".temp", ".safetensors", ".bin"))
        or "credential" in lowered
        or "model_cache" in lowered
        or "huggingface" in lowered
    )


@dataclass
class _SourceFile:
    source: Path
    destination: Path
    source_display: str
    destination_display: str
    size_bytes: int
    sha256: str


@dataclass
class _Mapping:
    source: Path
    destination: Path
    source_display: str
    destination_display: str
    copy_kind: str
    files: list[_SourceFile]
    total_bytes: int
    tree_sha256: str
    category: str
    status: str = "planned"


KNOWN_DIRECTORY_MAPPINGS = (
    (".local_data/verilog-eval-main/dataset_spec-to-rtl", "raw/verilog_eval/upstream/dataset_spec-to-rtl", "raw_source"),
    ("review/rtl_generation_normalization_batches", "runs/manual_rtl_teacher/{run_id}/normalization/packets", "workflow_public"),
    (".local_data/manual_task_normalization", "runs/manual_rtl_teacher/{run_id}/normalization/responses", "workflow_private"),
    (".local_data/rtl_generation_verification_assets", "runs/manual_rtl_teacher/{run_id}/private_assets", "workflow_private"),
    ("review/rtl_teacher_generation_packets", "runs/manual_rtl_teacher/{run_id}/teacher/packets", "workflow_public"),
    (".local_data/manual_teacher_responses", "runs/manual_rtl_teacher/{run_id}/teacher/responses", "workflow_private"),
    (".local_data/rtl_candidate_verification", "runs/manual_rtl_teacher/{run_id}/verification", "verification"),
    ("review/rtl_teacher_repair_packets", "runs/manual_rtl_teacher/{run_id}/repairs", "workflow_public"),
)
KNOWN_FILE_MAPPINGS = (
    ("review/rtl_generation_pilot/generation_tasks.jsonl", "runs/manual_rtl_teacher/{run_id}/tasks/generation_tasks.jsonl", "workflow_public"),
    ("review/rtl_generation_pilot/verification_assets.jsonl", "runs/manual_rtl_teacher/{run_id}/tasks/verification_assets.jsonl", "workflow_private"),
    ("review/rtl_generation_pilot/candidate_records.jsonl", "runs/manual_rtl_teacher/{run_id}/teacher/candidate_records.jsonl", "workflow_public"),
    ("review/rtl_generation_pilot/generation_attempts.jsonl", "runs/manual_rtl_teacher/{run_id}/verification/generation_attempts.jsonl", "verification"),
)


def _source_files(source: Path, destination: Path, data_root: Path, source_display: str, destination_display: str) -> list[_SourceFile]:
    result: list[_SourceFile] = []
    if source.is_symlink() or _contains_symlink(source):
        raise WorkspaceError(f"migration source contains a symlink: {source_display}")
    if source.is_file():
        if _forbidden_source_name(source.name):
            return result
        result.append(_SourceFile(source, destination, source_display, destination_display, source.stat().st_size, _sha256_file(source)))
        return result
    if not source.is_dir():
        raise WorkspaceError(f"known migration source is not a regular file or directory: {source_display}")
    pending = [(source, destination)]
    while pending:
        current, current_destination = pending.pop()
        if current.is_symlink() or _contains_symlink(current):
            raise WorkspaceError(f"migration source contains a symlink: {source_display}")
        children = sorted(current.iterdir(), key=lambda item: item.name)
        for child in children:
            if child.is_symlink():
                raise WorkspaceError(f"migration source contains a symlink: {source_display}")
            if _forbidden_source_name(child.name):
                continue
            target_name = child.name
            if source.name == "rtl_candidate_verification" and current == source:
                match = re.fullmatch(r"run_00([1-4])", child.name)
                if match:
                    target_name = f"attempt_0{match.group(1)}"
            target = current_destination / target_name
            if child.is_dir():
                pending.append((child, target))
            elif child.is_file():
                result.append(_SourceFile(child, target, source_display, destination_display, child.stat().st_size, _sha256_file(child)))
            else:
                raise WorkspaceError(f"known migration source contains a special file: {source_display}")
    result.sort(key=lambda item: item.destination.as_posix())
    return result


def _mapping_tree_digest(files: list[_SourceFile], source: Path) -> str:
    rows: list[tuple[str, int, str]] = []
    for item in files:
        relative = item.source.relative_to(source).as_posix() if source.is_dir() else source.name
        rows.append((relative, item.size_bytes, item.sha256))
    return _directory_digest(rows)


def _mapping_collision(mapping: _Mapping, additional_files: Iterable[_SourceFile] = ()) -> str | None:
    destination = mapping.destination
    if _contains_symlink(destination):
        return f"destination contains a symlink: {mapping.destination_display}"
    if destination.exists() and mapping.copy_kind == "directory" and not destination.is_dir():
        return f"file/directory collision at {mapping.destination_display}"
    if destination.exists() and mapping.copy_kind == "file" and destination.is_dir():
        return f"file/directory collision at {mapping.destination_display}"
    expected_items = list(mapping.files) + list(additional_files)
    expected_files = {item.destination for item in expected_items}
    if mapping.copy_kind == "file":
        if not destination.exists():
            return None
        if _sha256_file(destination) != mapping.files[0].sha256:
            return f"different destination bytes at {mapping.destination_display}"
        return None
    if not destination.exists():
        return None
    actual_paths: list[Path] = []
    pending = [destination]
    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            if child.is_symlink():
                return f"destination contains a symlink: {mapping.destination_display}"
            actual_paths.append(child)
            if child.is_dir():
                pending.append(child)
    expected_dirs = {destination}
    for expected in expected_files:
        current = expected.parent
        while _is_relative_to(current, destination):
            expected_dirs.add(current)
            if current == destination:
                break
            current = current.parent
    for path in actual_paths:
        if path.is_dir():
            if path not in expected_dirs:
                return f"unknown destination content at {mapping.destination_display}"
            continue
        if path not in expected_files:
            return f"unknown destination content at {mapping.destination_display}"
        expected = next(item for item in expected_items if item.destination == path)
        if not path.is_file() or _sha256_file(path) != expected.sha256:
            return f"different destination bytes at {mapping.destination_display}"
    return None


def _destination_root_collision(root: Path, expected_items: list[_SourceFile], display: str) -> str | None:
    """Check one publication root, including mappings made of single files."""

    if _contains_symlink(root):
        return f"destination contains a symlink: {display}"
    if not root.exists():
        return None
    if not root.is_dir():
        return f"file/directory collision at {display}"
    expected_files = {item.destination for item in expected_items}
    expected_dirs = {root}
    for expected in expected_files:
        current = expected.parent
        while _is_relative_to(current, root):
            expected_dirs.add(current)
            if current == root:
                break
            current = current.parent
    pending = [root]
    while pending:
        current = pending.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            if child.is_symlink():
                return f"destination contains a symlink: {display}"
            if child.is_dir():
                if child not in expected_dirs:
                    return f"unknown destination content at {display}"
                pending.append(child)
                continue
            if child not in expected_files:
                return f"unknown destination content at {display}"
            expected = next(item for item in expected_items if item.destination == child)
            if not child.is_file() or _sha256_file(child) != expected.sha256:
                return f"different destination bytes at {display}"
    return None


def _collect_mappings(data_root: Path, run_id: str) -> tuple[list[_Mapping], list[str], set[str]]:
    mappings: list[_Mapping] = []
    missing: list[str] = []
    covered: set[str] = set()
    def add(source_relative: str, destination_relative: str, kind: str, source: Path, destination: Path) -> None:
        source_display = f"{_root_label(data_root)}/{source_relative}"
        destination_display = f"{_root_label(data_root)}/{destination_relative}"
        files = _source_files(source, destination, data_root, source_display, destination_display)
        if source.is_dir():
            copy_kind = "directory"
            tree_hash = _mapping_tree_digest(files, source)
        else:
            copy_kind = "file"
            tree_hash = files[0].sha256 if files else _sha256_bytes(b"")
        mapping = _Mapping(source, destination, source_display, destination_display, copy_kind, files, sum(item.size_bytes for item in files), tree_hash, kind)
        mappings.append(mapping)
        if source.is_dir():
            for item in files:
                covered.add(_display_path(data_root, item.source))
                current = item.source.parent
                while _is_relative_to(current, source) and current != source:
                    covered.add(_display_path(data_root, current))
                    current = current.parent
            covered.add(source_display)
        else:
            covered.add(source_display)

    for source_relative, destination_relative, kind in KNOWN_DIRECTORY_MAPPINGS:
        source = data_root.joinpath(*source_relative.split("/"))
        destination = data_root.joinpath(*destination_relative.format(run_id=run_id).split("/"))
        if not source.exists() and not source.is_symlink():
            missing.append(f"{_root_label(data_root)}/{source_relative}")
            continue
        add(source_relative, destination_relative.format(run_id=run_id), kind, source, destination)

    for source_relative, destination_relative, kind in KNOWN_FILE_MAPPINGS:
        source = data_root.joinpath(*source_relative.split("/"))
        destination_relative = destination_relative.format(run_id=run_id)
        destination = data_root.joinpath(*destination_relative.split("/"))
        if not source.exists() and not source.is_symlink():
            missing.append(f"{_root_label(data_root)}/{source_relative}")
            continue
        add(source_relative, destination_relative, kind, source, destination)

    checkout = data_root / ".local_data" / "verilog-eval-main"
    if checkout.exists() and not checkout.is_symlink():
        for pattern in ("LICENSE*", "README*"):
            matches = sorted(item for item in checkout.glob(pattern) if item.is_file())
            if not matches:
                missing.append(f"{_root_label(data_root)}/.local_data/verilog-eval-main/{pattern}")
            for source in matches:
                relative = source.relative_to(data_root).as_posix()
                destination_relative = "raw/verilog_eval/upstream/" + source.name
                add(relative, destination_relative, "raw_source", source, data_root / destination_relative)
    else:
        missing.extend([
            f"{_root_label(data_root)}/.local_data/verilog-eval-main/LICENSE*",
            f"{_root_label(data_root)}/.local_data/verilog-eval-main/README*",
        ])
    all_files = [item for mapping in mappings for item in mapping.files]
    for mapping in mappings:
        additional = [
            item for item in all_files
            if item not in mapping.files and _is_relative_to(item.destination, mapping.destination)
        ]
        collision = _mapping_collision(mapping, additional)
        if collision:
            mapping.status = "collision"
        elif mapping.files and all(item.destination.exists() and item.destination.is_file() and _sha256_file(item.destination) == item.sha256 for item in mapping.files):
            mapping.status = "already_present"
    publication_roots: dict[Path, list[_Mapping]] = {}
    for mapping in mappings:
        root_path = mapping.destination if mapping.copy_kind == "directory" else mapping.destination.parent
        publication_roots.setdefault(root_path, []).append(mapping)
    for root_path, root_mappings in publication_roots.items():
        expected = [
            item
            for mapping in mappings
            for item in mapping.files
            if _is_relative_to(item.destination, root_path)
        ]
        display = _display_path(data_root, root_path)
        collision = _destination_root_collision(root_path, expected, display)
        if collision:
            for mapping in root_mappings:
                mapping.status = "collision"
    mappings.sort(key=lambda item: (item.destination_display, item.source_display))
    return mappings, sorted(set(missing)), covered


def _migration_unknown_paths(data_root: Path, covered: set[str]) -> tuple[list[str], dict[str, Any]]:
    inventory = build_inventory(data_root)
    unknown: list[str] = []
    for entry in inventory["entries"]:
        path = entry["path"]
        if path in covered:
            continue
        if entry["category"] == "unknown" or entry["legacy"]:
            unknown.append(path)
    return sorted(set(unknown)), inventory


def _mapping_report(mapping: _Mapping) -> dict[str, Any]:
    return {
        "source_path": mapping.source_display,
        "destination_path": mapping.destination_display,
        "category": mapping.category,
        "copy_kind": mapping.copy_kind,
        "file_count": len(mapping.files),
        "total_bytes": mapping.total_bytes,
        "tree_sha256": mapping.tree_sha256,
        "status": mapping.status,
    }


def _migration_id(run_id: str, mappings: list[_Mapping], missing: list[str], unknown: list[str]) -> str:
    identity = {
        "layout_version": LAYOUT_VERSION,
        "run_id": run_id,
        "mappings": [{"source": item.source_display, "destination": item.destination_display, "hash": item.tree_sha256} for item in mappings],
        "missing": missing,
        "unknown": unknown,
    }
    return f"data_workspace_{run_id}_{_sha256_bytes(_json_bytes(identity))[:16]}"


def build_migration_plan(data_root: Path, run_id: str, *, mode: str = "dry_run") -> dict[str, Any]:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise WorkspaceError("run-id must match ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    if mode not in {"dry_run", "apply"}:
        raise WorkspaceError("migration mode must be dry_run or apply")
    root = _absolute(data_root)
    mappings, missing, covered = _collect_mappings(root, run_id)
    unknown, inventory = _migration_unknown_paths(root, covered)
    collisions = sorted({f"{item.source_display}: {item.status}" for item in mappings if item.status == "collision"})
    summary = {
        "mapping_count": len(mappings),
        "planned_file_count": sum(len(item.files) for item in mappings),
        "planned_bytes": sum(item.total_bytes for item in mappings),
        "legacy_path_count": inventory["summary"]["legacy_entry_count"],
        "unknown_path_count": len(unknown),
        "raw_source_bytes": inventory["summary"]["raw_source_bytes"],
        "collision_count": len(collisions),
        "missing_optional_source_count": len(missing),
        "verilog_eval_checkout_detected": bool((root / ".local_data" / "verilog-eval-main" / "dataset_spec-to-rtl").is_dir()),
    }
    return {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "layout_version": LAYOUT_VERSION,
        "migration_id": _migration_id(run_id, mappings, missing, unknown),
        "run_id": run_id,
        "mode": mode,
        "mappings": [_mapping_report(item) for item in mappings],
        "missing_optional_sources": missing,
        "collisions": collisions,
        "unknown_paths": unknown,
        "summary": summary,
    }


def _source_file_map(mappings: list[_Mapping]) -> dict[Path, _SourceFile]:
    result: dict[Path, _SourceFile] = {}
    for mapping in mappings:
        for item in mapping.files:
            if item.destination in result and result[item.destination].sha256 != item.sha256:
                raise WorkspaceError(f"two mappings claim different bytes for one destination")
            result[item.destination] = item
    return result


def _copy_stream(source: Path, destination: Path, expected_size: int, expected_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as source_handle, destination.open("wb") as destination_handle:
        for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
            total += len(chunk)
            digest.update(chunk)
            destination_handle.write(chunk)
        destination_handle.flush()
        os.fsync(destination_handle.fileno())
    if total != expected_size or digest.hexdigest() != expected_hash:
        raise WorkspaceError(f"source changed while copying {source.name}")
    os.chmod(destination, 0o644)


def _copy_existing_tree(source_root: Path, destination_root: Path) -> None:
    if not source_root.exists():
        return
    pending = [(source_root, destination_root)]
    while pending:
        current, target = pending.pop()
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(current.iterdir(), key=lambda item: item.name):
            # A nested destination root may have a sibling staging directory
            # created earlier in this transaction. It is transaction state,
            # not destination content, and must never be copied into another
            # staged root.
            if child.name.startswith(".") and ".migration-" in child.name:
                continue
            if child.is_symlink():
                raise WorkspaceError("destination contains a symlink")
            child_target = target / child.name
            if child.is_dir():
                pending.append((child, child_target))
            elif child.is_file():
                _copy_stream(child, child_target, child.stat().st_size, _sha256_file(child))
            else:
                raise WorkspaceError("destination contains a special file")


def _remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            _remove_tree(child)
        path.rmdir()
    else:
        path.unlink()


def _canonical_run_root(data_root: Path, run_id: str) -> Path:
    return _absolute(data_root) / "runs" / RUN_WORKFLOW / run_id


def _require_valid_canonical_run(data_root: Path, run_id: str) -> None:
    run_root = _canonical_run_root(data_root, run_id)
    report, code = validate_manual_rtl_run(run_root)
    if code:
        detail = "; ".join(report["errors"]) or "manifest or directory structure is invalid"
        raise WorkspaceError(
            f"migration apply requires an initialized canonical run at "
            f"data/runs/{RUN_WORKFLOW}/{run_id}/run_manifest.json: {detail}"
        )


def _apply_mappings(
    data_root: Path,
    run_id: str,
    *,
    post_publish_validate: Callable[[], None] | None = None,
) -> list[_Mapping]:
    mappings, missing, covered = _collect_mappings(data_root, run_id)
    del missing, covered
    if any(item.status == "collision" for item in mappings):
        raise WorkspaceError("migration preflight found destination collisions")
    for mapping in mappings:
        for item in mapping.files:
            if item.source.is_symlink() or _contains_symlink(item.source):
                raise WorkspaceError("migration source contains a symlink")
            if not item.source.is_file() or _sha256_file(item.source) != item.sha256 or item.source.stat().st_size != item.size_bytes:
                raise WorkspaceError(f"source changed after preflight: {item.source_display}")
    source_by_destination = _source_file_map(mappings)
    preexisting = {
        mapping.source_display: bool(mapping.files) and all(
            item.destination.exists() and item.destination.is_file() and _sha256_file(item.destination) == item.sha256
            for item in mapping.files
        )
        for mapping in mappings
    }
    roots: dict[Path, list[Path]] = {}
    for mapping in mappings:
        root = mapping.destination if mapping.copy_kind == "directory" else mapping.destination.parent
        roots.setdefault(root, []).append(mapping.destination)
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    published: list[Path] = []
    created_destination_dirs: set[Path] = set()
    try:
        for destination_root, _ in sorted(roots.items(), key=lambda item: item[0].as_posix()):
            if _contains_symlink(destination_root):
                raise WorkspaceError("destination contains a symlink")
            if destination_root.exists() and not destination_root.is_dir():
                raise WorkspaceError("destination root is not a directory")
            if destination_root.exists():
                # A root with only already-present bytes does not need a
                # publication, which also avoids replacing user data for a
                # no-op migration.
                relevant = [item for item in source_by_destination.values() if _is_relative_to(item.destination, destination_root)]
                if all(item.destination.exists() and item.destination.is_file() and _sha256_file(item.destination) == item.sha256 for item in relevant):
                    continue
            created_destination_dirs.update(_missing_directory_ancestors(destination_root.parent))
            _ensure_directory(destination_root.parent, "migration destination parent")
            stage = Path(tempfile.mkdtemp(prefix=f".{destination_root.name}.migration-", dir=os.fspath(destination_root.parent)))
            os.chmod(stage, 0o755)
            staged[destination_root] = stage
            _copy_existing_tree(destination_root, stage)
            for item in sorted(source_by_destination.values(), key=lambda item: item.destination.as_posix()):
                if not _is_relative_to(item.destination, destination_root):
                    continue
                target = stage / item.destination.relative_to(destination_root)
                if target.exists():
                    if not target.is_file() or _sha256_file(target) != item.sha256:
                        raise WorkspaceError(f"different destination bytes at {item.destination_display}")
                else:
                    _copy_stream(item.source, target, item.size_bytes, item.sha256)
                if _sha256_file(target) != item.sha256:
                    raise WorkspaceError(f"destination hash mismatch at {item.destination_display}")

        # Every source has been staged and verified before any destination is
        # published.  Rename each complete root and retain a rollback sibling
        # until all roots have published successfully.
        for destination_root, stage in sorted(staged.items(), key=lambda item: (-len(item[0].parts), item[0].as_posix())):
            backup: Path | None = None
            if destination_root.exists():
                # Keep backups outside canonical run roots so the post-copy
                # validator observes the exact run structure. They remain on
                # the same filesystem and are retained until validation and
                # all publication steps succeed.
                backup = Path(tempfile.mkdtemp(prefix=f".{destination_root.name}.migration-backup-", dir=os.fspath(data_root)))
                _remove_tree(backup)
                os.replace(destination_root, backup)
            backups[destination_root] = backup
            try:
                os.replace(stage, destination_root)
            except BaseException:
                if backup is not None and backup.exists() and not destination_root.exists():
                    os.replace(backup, destination_root)
                raise
            published.append(destination_root)
            staged.pop(destination_root, None)
        if post_publish_validate is not None:
            post_publish_validate()
        for destination_root, backup in backups.items():
            if backup is not None:
                _remove_tree(backup)
        for mapping in mappings:
            mapping.status = "already_present" if preexisting[mapping.source_display] else "copied"
        return mappings
    except BaseException:
        for destination_root, stage in list(staged.items()):
            _remove_tree(stage)
        for destination_root in reversed(published):
            backup = backups.get(destination_root)
            if destination_root.exists():
                _remove_tree(destination_root)
            if backup is not None and backup.exists():
                os.replace(backup, destination_root)
        for destination_root, backup in backups.items():
            if backup is not None and backup.exists() and not destination_root.exists():
                os.replace(backup, destination_root)
        for directory in sorted(created_destination_dirs, key=lambda item: (-len(item.parts), item.as_posix())):
            if directory.exists() and directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        raise


def migrate_legacy_rtl_data_workspace(data_root: Path, run_id: str, *, apply: bool = False, output: Path | None = None, force: bool = False) -> dict[str, Any]:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise WorkspaceError("run-id must match ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    if apply:
        _require_valid_canonical_run(data_root, run_id)
    if output is not None:
        # Check report collisions before an apply can publish any destination.
        _safe_output_path(output, force=force)
    mode = "apply" if apply else "dry_run"
    plan = build_migration_plan(data_root, run_id, mode=mode)
    if plan["collisions"]:
        raise WorkspaceError("migration has destination collisions")
    if apply:
        mappings = _apply_mappings(
            _absolute(data_root),
            run_id,
            post_publish_validate=lambda: _require_valid_canonical_run(data_root, run_id),
        )
        plan["mappings"] = [_mapping_report(item) for item in mappings]
        plan["summary"]["already_present_mapping_count"] = sum(item.status == "already_present" for item in mappings)
        plan["summary"]["copied_mapping_count"] = sum(item.status == "copied" for item in mappings)
    if output is not None:
        atomic_write_json(output, plan, force=force)
    return plan


__all__ = [
    "CANONICAL_RUN_PATHS",
    "CATEGORIES",
    "INVENTORY_SCHEMA_VERSION",
    "LAYOUT_VERSION",
    "MIGRATION_SCHEMA_VERSION",
    "RUN_MANIFEST_SCHEMA_VERSION",
    "WorkspaceError",
    "atomic_write_json",
    "build_inventory",
    "build_migration_plan",
    "initialize_manual_rtl_run",
    "inventory_data_workspace",
    "migrate_legacy_rtl_data_workspace",
    "validate_manual_rtl_run",
]
