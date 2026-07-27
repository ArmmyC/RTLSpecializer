from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.dataset.rtl_manual_teacher_verification import (
    export_teacher_generation_packets,
    prepare_candidate_verification,
    validate_teacher_candidate_batch,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "manual_rtl_teacher"


def initial_flow(tmp_path: Path):
    packet_dir = tmp_path / "packets"
    result, code = export_teacher_generation_packets(
        FIXTURE_ROOT / "generation_tasks.jsonl", packet_dir
    )
    assert code == 0, result
    candidates = tmp_path / "candidates.jsonl"
    result, code = validate_teacher_candidate_batch(
        packet_dir / "packet_0001.json",
        FIXTURE_ROOT / "responses" / "valid_initial_response.json",
        private_assets_path=FIXTURE_ROOT / "verification_assets.jsonl",
        private_assets_root=FIXTURE_ROOT / "private_assets",
        output_path=candidates,
    )
    assert code == 0, result
    run = tmp_path / "run"
    result, code = prepare_candidate_verification(
        FIXTURE_ROOT / "generation_tasks.jsonl",
        FIXTURE_ROOT / "verification_assets.jsonl",
        FIXTURE_ROOT / "private_assets",
        candidates,
        run,
    )
    assert code == 0, result
    return packet_dir, candidates, run


def evidence_for_plan(run: Path, *, accepted: bool = False) -> Path:
    plan = json.loads((run / "verification_plan.jsonl").read_text(encoding="utf-8").splitlines()[0])
    candidate_passes = accepted
    evidence = {
        "schema_version": "rtl_candidate_evidence_v0.1",
        "candidate_id": plan["candidate_id"], "task_id": plan["task_id"], "source_id": plan["source_id"],
        "attempt": plan["attempt"], "top_module": plan["top_module"], "testbench_top": "tb",
        "simulation_result_contract": "mismatch_count_v1", "requested_checks": plan["requested_checks"],
        "input_hashes": plan["expected_hashes"],
        "toolchain": {name: {"available": name in {"iverilog", "vvp"}, "version": "synthetic" if name in {"iverilog", "vvp"} else None} for name in ("iverilog", "vvp", "verilator", "yosys")},
        "checks": {
            "compile": {"candidate": {"attempted": True, "passed": True, "reason": None}},
            "simulation": {"candidate_passes": {"attempted": True, "passed": candidate_passes, "reason": None if candidate_passes else "functional_mismatch"}},
            "lint": {"candidate": {"attempted": False, "passed": None, "reason": "not_requested"}},
            "synthesis": {"candidate": {"attempted": False, "passed": None, "reason": "not_requested"}},
        },
        "mismatch_summary": {"contract": "mismatch_count_v1", "reported_counts": [0 if accepted else 1], "reported_sample_counts": [1], "maximum_count": 0 if accepted else 1, "timeout_reported": False},
        "failure_category": "passed" if accepted else "functional_mismatch", "accepted": accepted,
        "diagnostics": [] if accepted else ["The bounded mismatch count was nonzero."],
    }
    path = run / "candidate_evidence.jsonl"
    path.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path
