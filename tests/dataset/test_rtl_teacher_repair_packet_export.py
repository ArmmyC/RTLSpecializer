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


def test_non_candidate_failures_do_not_export_repair_packets(tmp_path: Path) -> None:
    _, candidates, run = initial_flow(tmp_path)
    base = json.loads(evidence_for_plan(run).read_text(encoding="utf-8"))
    cases = {
        "simulation_result_missing": {
            "checks": {
                **base["checks"],
                "simulation": {
                    "candidate_passes": {
                        "attempted": True,
                        "passed": False,
                        "reason": "simulation_result_missing",
                    }
                },
            },
            "mismatch_summary": {
                "contract": "mismatch_count_v1",
                "reported_counts": [],
                "reported_sample_counts": [],
                "maximum_count": None,
                "timeout_reported": False,
            },
        },
        "tool_unavailable": {
            "checks": {
                **base["checks"],
                "compile": {
                    "candidate": {
                        "attempted": False,
                        "passed": None,
                        "reason": "tool_unavailable",
                    }
                },
                "simulation": {
                    "candidate_passes": {
                        "attempted": False,
                        "passed": None,
                        "reason": "tool_unavailable",
                    }
                },
            },
            "mismatch_summary": {
                "contract": "mismatch_count_v1",
                "reported_counts": [],
                "reported_sample_counts": [],
                "maximum_count": None,
                "timeout_reported": False,
            },
        },
        "internal_error": {
            "checks": {
                **base["checks"],
                "compile": {
                    "candidate": {
                        "attempted": True,
                        "passed": False,
                        "reason": "internal_error",
                    }
                },
                "simulation": {
                    "candidate_passes": {
                        "attempted": False,
                        "passed": None,
                        "reason": "internal_error",
                    }
                },
            },
            "mismatch_summary": {
                "contract": "mismatch_count_v1",
                "reported_counts": [],
                "reported_sample_counts": [],
                "maximum_count": None,
                "timeout_reported": False,
            },
        },
    }
    for category, mutation in cases.items():
        evidence = {
            **base,
            "accepted": False,
            "failure_category": category,
            "diagnostics": [],
            **mutation,
        }
        evidence_path = tmp_path / f"{category}.jsonl"
        evidence_path.write_text(json.dumps(evidence) + "\n", encoding="utf-8")
        attempts = tmp_path / f"{category}.attempts.jsonl"
        result, code = ingest_candidate_evidence(run / "verification_plan.jsonl", evidence_path, attempts)
        assert code == 0, result
        output = tmp_path / f"{category}.repairs"
        result, code = export_teacher_repair_packets(
            FIXTURE_ROOT / "generation_tasks.jsonl", candidates, attempts, output
        )
        assert code == 0, result
        assert result["repairable_attempts"] == 0
        assert not output.exists()
