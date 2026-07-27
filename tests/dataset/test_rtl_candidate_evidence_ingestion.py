from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_manual_teacher_verification import ingest_candidate_evidence
from tests.dataset.manual_rtl_teacher_helpers import evidence_for_plan, initial_flow


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
