from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dataset.rtl_manual_teacher_verification import ingest_candidate_evidence
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT, evidence_for_plan, initial_flow


def test_functional_mismatch_is_ingested_as_nonaccepted_attempt(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    evidence = evidence_for_plan(run)
    output = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output)
    assert code == 0, result
    attempt = json.loads(output.read_text(encoding="utf-8"))
    assert attempt["accepted"] is False
    assert attempt["failure_category"] == "functional_mismatch"
    assert "input_hashes" not in attempt


def test_committed_passed_fixture_ingests_unchanged(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    output = tmp_path / "accepted-attempts.jsonl"
    result, code = ingest_candidate_evidence(
        run / "verification_plan.jsonl",
        FIXTURE_ROOT / "evidence/passed.jsonl",
        output,
    )
    assert code == 0, result
    attempt = json.loads(output.read_text(encoding="utf-8"))
    assert attempt["accepted"] is True
    assert attempt["failure_category"] == "passed"
    assert attempt["mismatch_summary"]["reported_counts"] == [0]
    assert attempt["mismatch_summary"]["reported_sample_counts"] == [1]


@pytest.mark.parametrize("sample_counts", ([20], [None], [20, 40]))
def test_accepted_mismatch_sample_counts_keep_rtlbench_shape(tmp_path: Path, sample_counts: list[int | None]) -> None:
    _, _, run = initial_flow(tmp_path)
    value = json.loads(evidence_for_plan(run, accepted=True).read_text(encoding="utf-8"))
    value["mismatch_summary"]["reported_counts"] = [0] * len(sample_counts)
    value["mismatch_summary"]["reported_sample_counts"] = sample_counts
    value["mismatch_summary"]["maximum_count"] = 0
    evidence = tmp_path / "accepted-shape.jsonl"
    evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
    result, code = ingest_candidate_evidence(
        run / "verification_plan.jsonl", evidence, tmp_path / "accepted-shape-attempts.jsonl"
    )
    assert code == 0, result


@pytest.mark.parametrize(
    "summary",
    (
        {"reported_counts": [], "reported_sample_counts": [], "maximum_count": None, "timeout_reported": False},
        {"reported_counts": [1], "reported_sample_counts": [20], "maximum_count": 1, "timeout_reported": False},
        {"reported_counts": [0, 0], "reported_sample_counts": [20], "maximum_count": 0, "timeout_reported": False},
        {"reported_counts": [0], "reported_sample_counts": [-1], "maximum_count": 0, "timeout_reported": False},
        {"reported_counts": [0], "reported_sample_counts": ["20"], "maximum_count": 0, "timeout_reported": False},
        {"reported_counts": [0], "reported_sample_counts": [20], "maximum_count": 0, "timeout_reported": True},
    ),
)
def test_invalid_accepted_mismatch_summaries_are_rejected(tmp_path: Path, summary: dict) -> None:
    _, _, run = initial_flow(tmp_path)
    value = json.loads(evidence_for_plan(run, accepted=True).read_text(encoding="utf-8"))
    value["mismatch_summary"] = {"contract": "mismatch_count_v1", **summary}
    evidence = tmp_path / "invalid-accepted-summary.jsonl"
    evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
    result, code = ingest_candidate_evidence(
        run / "verification_plan.jsonl", evidence, tmp_path / "invalid-accepted-summary-attempts.jsonl"
    )
    assert code != 0, result


def test_evidence_hash_mismatch_is_rejected_without_output(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    evidence = evidence_for_plan(run)
    value = json.loads(evidence.read_text(encoding="utf-8"))
    value["input_hashes"]["candidate_rtl_sha256"] = "0" * 64
    evidence.write_text(json.dumps(value) + "\n", encoding="utf-8")
    output = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, output)
    assert code != 0
    assert not output.exists()
