from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dataset.rtl_generation_batch_metrics import (
    BatchMetricsError,
    SCHEMA_VERSION,
    build_batch_metrics,
    write_batch_metrics,
)


def test_batch_metrics_record_bounded_train_only_outcomes(tmp_path: Path) -> None:
    metrics = build_batch_metrics(
        batch_id="verilog_eval_generation_v003_batch20_v001",
        correction_version="assetfix_v004",
        selected_tasks=20,
        qualified_assets=19,
        normalization_accepted=19,
        first_attempt_passes=16,
        repair_successes=2,
        final_accepted_rows=18,
        compile_failures=1,
        functional_mismatches=1,
        timeouts=0,
        privacy_or_provenance_failures=0,
        runner_failures=0,
        asset_failures=1,
        warnings_present=1,
        qualification_mutations_total=57,
        qualification_mutations_detected=57,
        source_commit="a" * 40,
        source_tree_sha256="b" * 64,
        frozen_split_sha256="c" * 64,
        qualification_binding_sha256="d" * 64,
    )

    assert metrics["schema_version"] == SCHEMA_VERSION
    assert metrics["split"] == "train"
    assert metrics["final_accepted_rows"] == 18
    assert metrics["promotion_allowed"] is False
    assert metrics["qualification_mutations_detected"] == 57

    path = tmp_path / "metrics.json"
    digest = write_batch_metrics(path, metrics)
    assert len(digest) == 64
    assert json.loads(path.read_text(encoding="utf-8"))["batch_id"] == metrics["batch_id"]
    with pytest.raises(BatchMetricsError):
        write_batch_metrics(path, metrics)


def test_batch_metrics_rejects_unsafe_scope_or_inconsistent_counts() -> None:
    with pytest.raises(BatchMetricsError, match="train-only"):
        build_batch_metrics(
            batch_id="batch",
            correction_version="assetfix_v004",
            selected_tasks=1,
            qualified_assets=1,
            first_attempt_passes=1,
            repair_successes=0,
            final_accepted_rows=1,
            compile_failures=0,
            functional_mismatches=0,
            timeouts=0,
            privacy_or_provenance_failures=0,
            split="validation",
        )
    with pytest.raises(BatchMetricsError, match="exceeds qualified_assets"):
        build_batch_metrics(
            batch_id="batch",
            correction_version="assetfix_v004",
            selected_tasks=1,
            qualified_assets=1,
            first_attempt_passes=1,
            repair_successes=0,
            final_accepted_rows=2,
            compile_failures=0,
            functional_mismatches=0,
            timeouts=0,
            privacy_or_provenance_failures=0,
        )
