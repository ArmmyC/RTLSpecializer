"""Manifest adapter for local public dataset draft ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.dataset.adapters.base import DiscoveryResult, ImportOptions, ImportRejection, PublicDatasetAdapter, RawPublicExample
from scripts.dataset.constants import ARTIFACT_FIELDS, SOURCES, TASK_TYPES, USER_GOALS


MANIFEST_ARTIFACT_PATHS = {
    "rtl_code_path": "rtl_code",
    "before_rtl_code_path": "before_rtl_code",
    "after_rtl_code_path": "after_rtl_code",
    "testbench_path": "testbench",
    "lint_log_path": "lint_log",
    "synthesis_report_path": "synthesis_report",
    "toggle_report_path": "toggle_report",
}
REQUIRED_FIELDS = {"id", "source", "license", "design_family", "task_type", "user_goal", "artifacts", "provenance"}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_artifact(path_value: object, root: Path, options: ImportOptions) -> tuple[str | None, str | None]:
    if path_value is None:
        return None, None
    if not isinstance(path_value, str) or not path_value.strip():
        return None, "artifact path must be a non-empty string or null"
    raw_path = Path(path_value)
    if raw_path.is_absolute() and not options.allow_absolute_paths:
        return None, f"absolute artifact paths are rejected by default: {path_value}"
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    try:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
    except OSError as exc:
        return None, f"could not resolve artifact path {path_value!r}: {exc}"
    if not options.allow_outside_root and not _is_relative_to(resolved, resolved_root):
        return None, f"artifact path escapes input root: {path_value}"
    if not resolved.exists():
        return None, f"artifact file not found: {path_value}"
    if not resolved.is_file():
        return None, f"artifact path is not a regular file: {path_value}"
    try:
        if resolved.stat().st_size > options.max_artifact_bytes:
            return None, f"artifact file exceeds max bytes ({options.max_artifact_bytes}): {path_value}"
        return resolved.read_text(encoding="utf-8"), None
    except UnicodeError:
        return None, f"artifact file is not valid UTF-8 text: {path_value}"
    except OSError as exc:
        return None, f"could not read artifact file {path_value!r}: {exc}"


def _row_error(line: int, row: dict[str, Any], message: str) -> ImportRejection:
    source_id = row.get("id") if isinstance(row.get("id"), str) else None
    return ImportRejection(source_id, message, [f"line {line}: {message}"])


class ManifestAdapter(PublicDatasetAdapter):
    name = "manifest"

    def discover_examples(self, root: Path, options: ImportOptions) -> DiscoveryResult:
        manifest_path = root
        if manifest_path.is_dir():
            manifest_path = manifest_path / "manifest.jsonl"
        warnings: list[str] = []
        rejections: list[ImportRejection] = []
        examples: list[RawPublicExample] = []
        if not manifest_path.exists():
            return DiscoveryResult([], [ImportRejection(None, f"input path not found: {manifest_path}", [f"input path not found: {manifest_path}"])], warnings, 0)
        if not manifest_path.is_file():
            return DiscoveryResult([], [ImportRejection(None, f"input path is not a file: {manifest_path}", [f"input path is not a file: {manifest_path}"])], warnings, 0)

        manifest_root = manifest_path.parent
        discovered = 0
        try:
            lines = manifest_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            return DiscoveryResult([], [ImportRejection(None, f"could not read manifest: {exc}", [str(exc)])], warnings, 0)
        for line_number, raw in enumerate(lines, 1):
            if options.limit is not None and len(examples) >= options.limit:
                break
            if not raw.strip():
                continue
            discovered += 1
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                rejections.append(ImportRejection(None, "malformed manifest JSON", [f"line {line_number}: {exc.msg}"]))
                continue
            if not isinstance(row, dict):
                rejections.append(ImportRejection(None, "manifest row must be an object", [f"line {line_number}: row must be a JSON object"]))
                continue
            missing = sorted(REQUIRED_FIELDS - row.keys())
            if missing:
                rejections.append(_row_error(line_number, row, f"missing required manifest fields: {', '.join(missing)}"))
                continue
            source = options.source or row.get("source")
            license_value = options.license or row.get("license")
            task_type = row.get("task_type")
            user_goal = row.get("user_goal")
            if source not in SOURCES:
                rejections.append(_row_error(line_number, row, f"invalid source: {source!r}"))
                continue
            if task_type not in TASK_TYPES:
                rejections.append(_row_error(line_number, row, f"invalid task_type: {task_type!r}"))
                continue
            if user_goal not in USER_GOALS:
                rejections.append(_row_error(line_number, row, f"invalid user_goal: {user_goal!r}"))
                continue
            if not isinstance(row.get("id"), str) or not row["id"].strip():
                rejections.append(_row_error(line_number, row, "id must be a non-empty string"))
                continue
            if not isinstance(license_value, str) or not license_value.strip():
                rejections.append(_row_error(line_number, row, "license must be non-empty"))
                continue
            if not isinstance(row.get("design_family"), str) or not row["design_family"].strip():
                rejections.append(_row_error(line_number, row, "design_family must be non-empty"))
                continue
            artifacts_spec = row.get("artifacts")
            provenance = row.get("provenance")
            if not isinstance(artifacts_spec, dict):
                rejections.append(_row_error(line_number, row, "artifacts must be an object"))
                continue
            if not isinstance(provenance, dict):
                rejections.append(_row_error(line_number, row, "provenance must be an object"))
                continue
            artifact_text: dict[str, str] = {}
            artifact_errors: list[str] = []
            for path_field, artifact_field in MANIFEST_ARTIFACT_PATHS.items():
                value = artifacts_spec.get(path_field)
                text, error = _read_artifact(value, manifest_root, options)
                if error:
                    artifact_errors.append(error)
                elif text is not None:
                    artifact_text[artifact_field] = text
            if not artifact_text:
                artifact_errors.append("at least one artifact path must be non-null and readable")
            if artifact_errors:
                rejections.append(ImportRejection(row["id"], "artifact read failed", [f"line {line_number}: {item}" for item in artifact_errors]))
                continue
            for field in ARTIFACT_FIELDS:
                artifact_text.setdefault(field, None)  # type: ignore[arg-type]
            examples.append(RawPublicExample(
                source_id=row["id"],
                root=manifest_root,
                artifacts=artifact_text,
                source=source,
                license=license_value,
                design_family=row["design_family"],
                task_type=task_type,
                user_goal=user_goal,
                provenance=provenance,
                metadata={"manifest": str(manifest_path), "line": line_number},
            ))
        if not lines:
            rejections.append(ImportRejection(None, "manifest is empty", ["manifest is empty"]))
        return DiscoveryResult(examples, rejections, warnings, discovered)
