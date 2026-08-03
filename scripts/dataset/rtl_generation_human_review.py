"""Validate an attributable human review record for a verified RTL package.

The validator checks a reviewer-supplied record against the immutable public
package.  It never creates a human review decision and never changes package
status or promotion state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from datetime import datetime
from typing import Any

from scripts.dataset.rtl_generation_dataset import (
    SMOKE_SOURCE_IDS,
    SMOKE_TASK_IDS,
)


HUMAN_REVIEW_SCHEMA_VERSION = "rtl_generation_human_review_v0.2"
PACKAGE_ID = "rtl_generation_smoke_v001_retry_02"
PACKAGE_TREE_SHA256 = "c8a7c077674c7bd4b84891a37831804e2a2b8f761a56c9b1c3a7d22a8de32c15"
REVIEW_ROW_FIELDS = {
    "source_id",
    "task_id",
    "candidate_id",
    "candidate_sha256",
    "specification_preserved",
    "interface_correct",
    "rtl_behavior_consistent",
    "training_quality_acceptable",
    "private_content_detected",
    "assistant_rtl_only",
    "metadata_accurate",
    "review_exception_acknowledged",
    "review_outcome",
    "review_notes",
}
REVIEW_FIELDS = {
    "schema_version",
    "package_id",
    "package_tree_sha256",
    "reviewer",
    "review_status",
    "overall_decision",
    "promotion_allowed",
    "row_count",
    "rows",
    "review_exceptions",
}
REVIEWER_FIELDS = {
    "reviewer_type",
    "reviewer_id",
    "reviewed_at",
    "review_method",
}
PRIVATE_MARKERS = (
    "/home/",
    "/tmp/",
    "/root/",
    "private_assets",
    "reference.sv",
    "testbench.sv",
    "candidate_evidence",
    "mutation",
    "iverilog",
    "verilator",
    "yosys",
)
REVIEW_OUTCOMES = {
    "approved_for_experimental_training",
    "changes_requested",
    "rejected",
}
REVIEW_EXCEPTIONS_FIELDS = {
    "source_id",
    "code",
    "disposition",
    "details",
}
REVIEW_EXCEPTION_DISPOSITION = "human_accepted_for_experimental_training"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_tree_sha256(root: Path) -> str:
    """Return the same path-independent tree hash used by the smoke freeze."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package root must be a regular directory")
    records: list[bytes] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError("package contains a symlink")
            records.append(b"D\0" + path.relative_to(root).as_posix().encode("utf-8") + b"\n")
        for name in files:
            path = Path(current) / name
            metadata = path.lstat()
            if path.is_symlink():
                raise ValueError("package contains a symlink")
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("package contains a non-regular file")
            records.append(
                b"F\0"
                + path.relative_to(root).as_posix().encode("utf-8")
                + b"\0"
                + sha256_file(path).encode("ascii")
                + b"\n"
            )
    return hashlib.sha256(b"".join(records)).hexdigest()


def _load_json(path: Path) -> Any:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError(f"input must not be a symlink: {path.name}")
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {index} is not an object")
        rows.append(value)
    return rows


def _private_marker(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.casefold()
        return any(marker.casefold() in lowered for marker in PRIVATE_MARKERS)
    if isinstance(value, dict):
        return any(_private_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_private_marker(item) for item in value)
    return False


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to replace existing output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def validate_human_review(
    package_dir: Path,
    review_path: Path,
    *,
    expected_package_id: str = PACKAGE_ID,
    expected_package_tree_sha256: str = PACKAGE_TREE_SHA256,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    actual_tree_hash: str | None = None
    package_rows: list[dict[str, Any]] = []
    review: dict[str, Any] | None = None
    package_manifest: dict[str, Any] = {}
    try:
        actual_tree_hash = package_tree_sha256(package_dir)
        package_manifest = _load_json(package_dir / "manifest.json")
        package_rows = _load_jsonl(package_dir / "all.jsonl")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))
    try:
        review_value = _load_json(review_path)
        if not isinstance(review_value, dict):
            errors.append("human review record must be an object")
        else:
            review = review_value
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(str(exc))

    if actual_tree_hash != expected_package_tree_sha256:
        errors.append("package tree hash mismatch")
    if not isinstance(package_manifest, dict) or package_manifest.get("package_id") != expected_package_id:
        errors.append("package identity mismatch")
    if isinstance(package_manifest, dict) and package_manifest.get("promotion_allowed") is not False:
        errors.append("package promotion state is not conservative")
    if len(package_rows) != 5:
        errors.append("package must contain exactly five rows")

    if review is None:
        review = {}
    if set(review) != REVIEW_FIELDS:
        errors.append("human review record has an invalid field set")
    if review.get("schema_version") != HUMAN_REVIEW_SCHEMA_VERSION:
        errors.append("wrong human review schema version")
    if review.get("package_id") != expected_package_id:
        errors.append("review package ID mismatch")
    if review.get("package_tree_sha256") != expected_package_tree_sha256:
        errors.append("review package tree hash mismatch")
    if review.get("review_status") != "human_reviewed":
        errors.append("review status is not human_reviewed")
    if review.get("overall_decision") not in REVIEW_OUTCOMES:
        errors.append("overall decision is invalid")
    if review.get("promotion_allowed") is not False:
        errors.append("review promotion state is not conservative")
    if review.get("row_count") != 5:
        errors.append("review row count must be five")

    reviewer = review.get("reviewer")
    if not isinstance(reviewer, dict):
        errors.append("reviewer metadata is missing")
    else:
        if set(reviewer) != REVIEWER_FIELDS:
            errors.append("reviewer metadata has an invalid field set")
        if reviewer.get("reviewer_type") != "human":
            errors.append("reviewer_type must be human")
        for field in ("reviewer_id", "reviewed_at", "review_method"):
            if not isinstance(reviewer.get(field), str) or not reviewer[field].strip():
                errors.append(f"reviewer.{field} is missing")
        if not _valid_timestamp(reviewer.get("reviewed_at")):
            errors.append("reviewer.reviewed_at is not an ISO-8601 timestamp")
        if reviewer.get("review_method") != "manual_row_review":
            errors.append("review method must be manual_row_review")
        if _private_marker(reviewer):
            errors.append("reviewer metadata contains a private marker")

    review_rows = review.get("rows")
    if not isinstance(review_rows, list) or len(review_rows) != 5:
        errors.append("review rows must contain exactly five rows")
        review_rows = []
    for index, row in enumerate(review_rows, 1):
        if not isinstance(row, dict):
            errors.append(f"review row {index} is not an object")
            continue
        if set(row) != REVIEW_ROW_FIELDS:
            errors.append(f"review row {index} has an invalid field set")
        if _private_marker(row):
            errors.append(f"review row {index} contains a private marker")
        for field in (
            "specification_preserved",
            "interface_correct",
            "rtl_behavior_consistent",
            "training_quality_acceptable",
            "assistant_rtl_only",
        ):
            if row.get(field) is not True:
                errors.append(f"review row {index} did not confirm {field}")
        if row.get("private_content_detected") is not False:
            errors.append(f"review row {index} reports private content")
        if not isinstance(row.get("metadata_accurate"), bool):
            errors.append(f"review row {index} metadata_accurate is not boolean")
        if not isinstance(row.get("review_exception_acknowledged"), bool):
            errors.append(
                f"review row {index} review_exception_acknowledged is not boolean"
            )
        if row.get("review_outcome") not in REVIEW_OUTCOMES:
            errors.append(f"review row {index} has an invalid outcome")
        if not isinstance(row.get("review_notes"), str) or not row["review_notes"].strip():
            errors.append(f"review row {index} is missing review notes")

    review_exceptions = review.get("review_exceptions")
    if not isinstance(review_exceptions, list):
        errors.append("review_exceptions must be a list")
        review_exceptions = []
    exception_sources: set[str] = set()
    for index, exception in enumerate(review_exceptions, 1):
        if not isinstance(exception, dict):
            errors.append(f"review exception {index} is not an object")
            continue
        if set(exception) != REVIEW_EXCEPTIONS_FIELDS:
            errors.append(f"review exception {index} has an invalid field set")
        if _private_marker(exception):
            errors.append(f"review exception {index} contains a private marker")
        source_id = exception.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            errors.append(f"review exception {index} is missing source_id")
        elif source_id in exception_sources:
            errors.append(f"review exception {index} duplicates source_id")
        else:
            exception_sources.add(source_id)
        if exception.get("code") != "normalized_metadata_mismatch":
            errors.append(f"review exception {index} has an invalid code")
        if exception.get("disposition") != REVIEW_EXCEPTION_DISPOSITION:
            errors.append(f"review exception {index} has an invalid disposition")
        if not isinstance(exception.get("details"), str) or not exception["details"].strip():
            errors.append(f"review exception {index} is missing details")

    expected_ids = list(zip(SMOKE_SOURCE_IDS, SMOKE_TASK_IDS))
    if len(package_rows) == 5 and len(review_rows) == 5:
        row_outcomes: list[str] = []
        for index, ((source_id, task_id), package_row, review_row) in enumerate(
            zip(expected_ids, package_rows, review_rows), 1
        ):
            if package_row.get("source_id") != source_id or package_row.get("task_id") != task_id:
                errors.append(f"package row {index} is not in the pinned smoke order")
            for field in ("source_id", "task_id", "candidate_id", "candidate_sha256"):
                if review_row.get(field) != package_row.get(field):
                    errors.append(f"review row {index} does not match package {field}")
            outcome = review_row.get("review_outcome")
            if isinstance(outcome, str):
                row_outcomes.append(outcome)
            if review_row.get("metadata_accurate") is False:
                if not review_row.get("review_exception_acknowledged"):
                    errors.append(
                        f"review row {index} has an unacknowledged metadata exception"
                    )
                if source_id not in exception_sources:
                    errors.append(
                        f"review row {index} has no matching review exception"
                    )

        if row_outcomes:
            if "rejected" in row_outcomes:
                derived_decision = "rejected"
            elif "changes_requested" in row_outcomes:
                derived_decision = "changes_requested"
            else:
                derived_decision = "approved_for_experimental_training"
            if review.get("overall_decision") != derived_decision:
                errors.append("overall decision is not derived from row decisions")

    result = {
        "schema_version": HUMAN_REVIEW_SCHEMA_VERSION,
        "package_id": expected_package_id,
        "expected_package_tree_sha256": expected_package_tree_sha256,
        "actual_package_tree_sha256": actual_tree_hash,
        "review_path": review_path.as_posix(),
        "row_count": len(review_rows),
        "overall_decision": review.get("overall_decision"),
        "review_exception_count": len(review_exceptions),
        "human_review_complete": not errors,
        "promotion_allowed": False,
        "errors": sorted(set(errors)),
    }
    return result, 0 if not errors else 1


__all__ = [
    "HUMAN_REVIEW_SCHEMA_VERSION",
    "PACKAGE_ID",
    "PACKAGE_TREE_SHA256",
    "package_tree_sha256",
    "validate_human_review",
]
