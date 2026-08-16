"""Validate and serialize bounded RTL-generation batch metrics.

The metrics contract is metadata-only.  It records counts and rates from the
qualification, generation, and verification control planes; it never reads
RTL, testbenches, reference material, or execution logs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "rtl_generation_batch_metrics_v0.1"


class BatchMetricsError(ValueError):
    """Raised when batch metrics are incomplete or internally inconsistent."""


def _count(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BatchMetricsError(f"{label} must be a non-negative integer")
    return value


def _sha256(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise BatchMetricsError(f"{label} must be a SHA-256 string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise BatchMetricsError(f"{label} must be a SHA-256 string") from exc
    return value


def _commit(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 40:
        raise BatchMetricsError(f"{label} must be a commit SHA")
    try:
        int(value, 16)
    except ValueError as exc:
        raise BatchMetricsError(f"{label} must be a commit SHA") from exc
    return value


def build_batch_metrics(
    *,
    batch_id: str,
    correction_version: str,
    selected_tasks: int,
    qualified_assets: int,
    first_attempt_passes: int,
    repair_successes: int,
    final_accepted_rows: int,
    compile_failures: int,
    functional_mismatches: int,
    timeouts: int,
    privacy_or_provenance_failures: int,
    runner_failures: int = 0,
    asset_failures: int = 0,
    normalization_accepted: int | None = None,
    candidate_schema_rejections: int = 0,
    warnings_present: int = 0,
    qualification_mutations_total: int = 0,
    qualification_mutations_detected: int = 0,
    split: str = "train",
    source_commit: str | None = None,
    source_tree_sha256: str | None = None,
    frozen_split_sha256: str | None = None,
    qualification_binding_sha256: str | None = None,
    promotion_allowed: bool = False,
) -> dict[str, Any]:
    """Build a fail-closed, privacy-safe metrics record for one batch."""

    if not isinstance(batch_id, str) or not batch_id:
        raise BatchMetricsError("batch_id is required")
    if not isinstance(correction_version, str) or not correction_version:
        raise BatchMetricsError("correction_version is required")
    if split != "train":
        raise BatchMetricsError("batch metrics are train-only")
    if promotion_allowed is not False:
        raise BatchMetricsError("promotion_allowed must remain false")

    raw_values = {
        "selected_tasks": selected_tasks,
        "qualified_assets": qualified_assets,
        "first_attempt_passes": first_attempt_passes,
        "repair_successes": repair_successes,
        "final_accepted_rows": final_accepted_rows,
        "compile_failures": compile_failures,
        "functional_mismatches": functional_mismatches,
        "timeouts": timeouts,
        "privacy_or_provenance_failures": privacy_or_provenance_failures,
        "runner_failures": runner_failures,
        "asset_failures": asset_failures,
        "normalization_accepted": normalization_accepted,
        "candidate_schema_rejections": candidate_schema_rejections,
        "warnings_present": warnings_present,
        "qualification_mutations_total": qualification_mutations_total,
        "qualification_mutations_detected": qualification_mutations_detected,
    }
    values = {
        field: None if value is None else _count(value, field)
        for field, value in raw_values.items()
    }

    if values["qualified_assets"] > values["selected_tasks"]:
        raise BatchMetricsError("qualified_assets exceeds selected_tasks")
    if values["final_accepted_rows"] > values["qualified_assets"]:
        raise BatchMetricsError("final_accepted_rows exceeds qualified_assets")
    if values["first_attempt_passes"] > values["qualified_assets"]:
        raise BatchMetricsError("first_attempt_passes exceeds qualified_assets")
    if values["repair_successes"] > values["final_accepted_rows"]:
        raise BatchMetricsError("repair_successes exceeds final_accepted_rows")
    if values["qualification_mutations_detected"] > values["qualification_mutations_total"]:
        raise BatchMetricsError("detected qualification mutations exceed total mutations")
    if values["normalization_accepted"] is not None and values["normalization_accepted"] > values["qualified_assets"]:
        raise BatchMetricsError("normalization_accepted exceeds qualified_assets")

    metrics: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "correction_version": correction_version,
        "split": split,
        "selected_tasks": values["selected_tasks"],
        "qualified_assets": values["qualified_assets"],
        "normalization_accepted": values["normalization_accepted"],
        "first_attempt_passes": values["first_attempt_passes"],
        "repair_successes": values["repair_successes"],
        "final_accepted_rows": values["final_accepted_rows"],
        "compile_failures": values["compile_failures"],
        "functional_mismatches": values["functional_mismatches"],
        "timeouts": values["timeouts"],
        "privacy_or_provenance_failures": values["privacy_or_provenance_failures"],
        "runner_failures": values["runner_failures"],
        "asset_failures": values["asset_failures"],
        "candidate_schema_rejections": values["candidate_schema_rejections"],
        "warnings_present": values["warnings_present"],
        "qualification_mutations_total": values["qualification_mutations_total"],
        "qualification_mutations_detected": values["qualification_mutations_detected"],
        "compile_pass_rate": (
            (values["selected_tasks"] - values["compile_failures"]) / values["selected_tasks"]
            if values["selected_tasks"]
            else None
        ),
        "simulation_pass_rate": (
            values["final_accepted_rows"] / values["selected_tasks"]
            if values["selected_tasks"]
            else None
        ),
        "source_commit": _commit(source_commit, "source_commit"),
        "source_tree_sha256": _sha256(source_tree_sha256, "source_tree_sha256"),
        "frozen_split_sha256": _sha256(frozen_split_sha256, "frozen_split_sha256"),
        "qualification_binding_sha256": _sha256(qualification_binding_sha256, "qualification_binding_sha256"),
        "promotion_allowed": False,
    }
    return metrics


def write_batch_metrics(path: Path, metrics: dict[str, Any]) -> str:
    """Write one exclusive 0600 JSON metrics record and return its hash."""

    if metrics.get("schema_version") != SCHEMA_VERSION:
        raise BatchMetricsError("metrics schema mismatch")
    if path.exists() or path.is_symlink():
        raise BatchMetricsError(f"refusing to replace existing metrics: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = (json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
    return hashlib.sha256(content).hexdigest()


__all__ = ["BatchMetricsError", "SCHEMA_VERSION", "build_batch_metrics", "write_batch_metrics"]
