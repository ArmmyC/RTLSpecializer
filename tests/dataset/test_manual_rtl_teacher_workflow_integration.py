from __future__ import annotations

from pathlib import Path

from scripts.dataset.rtl_manual_teacher_verification import export_teacher_repair_packets, ingest_candidate_evidence, validate_teacher_candidate_batch
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT, evidence_for_plan, initial_flow


def test_synthetic_manual_flow_reaches_repair_candidate_without_execution(tmp_path: Path) -> None:
    packet_dir, candidates, run = initial_flow(tmp_path)
    evidence = evidence_for_plan(run)
    attempts = tmp_path / "attempts.jsonl"
    result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence, attempts)
    assert code == 0, result
    repairs = tmp_path / "repairs"
    result, code = export_teacher_repair_packets(FIXTURE_ROOT / "generation_tasks.jsonl", candidates, attempts, repairs)
    assert code == 0, result
    repaired_candidates = tmp_path / "repaired_candidates.jsonl"
    result, code = validate_teacher_candidate_batch(
        repairs / "packet_0001.json", FIXTURE_ROOT / "responses/valid_repair_response.json",
        private_assets_path=FIXTURE_ROOT / "verification_assets.jsonl", private_assets_root=FIXTURE_ROOT / "private_assets", output_path=repaired_candidates,
    )
    assert code == 0, result
    assert "attempt_02" in repaired_candidates.read_text(encoding="utf-8")
    assert packet_dir.joinpath("packet_0001.md").is_file()
