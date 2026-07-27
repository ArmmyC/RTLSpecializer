from __future__ import annotations

from pathlib import Path

import json

from scripts.dataset.rtl_manual_teacher_verification import export_teacher_repair_packets, ingest_candidate_evidence
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT, evidence_for_plan, initial_flow


def test_failed_attempt_exports_bounded_repair_packet(tmp_path: Path) -> None:
    _, candidates, run = initial_flow(tmp_path)
    evidence = evidence_for_plan(run)
    attempts = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, attempts)
    assert code == 0, result
    output = tmp_path / "repairs"
    result, code = export_teacher_repair_packets(FIXTURE_ROOT / "generation_tasks.jsonl", candidates, attempts, output)
    assert code == 0, result
    packet = (output / "packet_0001.json").read_text(encoding="utf-8")
    assert '"target_attempt": 2' in packet
    assert "reference.sv" not in packet
    assert "testbench.sv" not in packet
    assert "raw_logs" not in packet


def test_attempt_four_is_not_repairable(tmp_path: Path) -> None:
    _, candidates, run = initial_flow(tmp_path)
    evidence = evidence_for_plan(run)
    attempts = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, attempts)
    assert code == 0, result
    attempt = json.loads(attempts.read_text(encoding="utf-8"))
    attempt["attempt"] = 4
    attempt["candidate_id"] = "rtlgen_synthetic_example_attempt_04"
    attempts.write_text(json.dumps(attempt) + "\n", encoding="utf-8")
    # Candidate records are deliberately not fabricated for attempt four;
    # selection must stop before requiring a missing repair source.
    output = tmp_path / "repairs"
    result, code = export_teacher_repair_packets(FIXTURE_ROOT / "generation_tasks.jsonl", candidates, attempts, output)
    assert code != 0
    assert "contiguous" in result["errors"][0]
