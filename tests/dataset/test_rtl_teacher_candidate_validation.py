from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_manual_teacher_verification import export_teacher_generation_packets, validate_teacher_candidate_batch
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT


def test_valid_candidate_record_is_derived_locally(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packets"
    result, code = export_teacher_generation_packets(FIXTURE_ROOT / "generation_tasks.jsonl", packet_dir)
    assert code == 0, result
    output = tmp_path / "candidates.jsonl"
    result, code = validate_teacher_candidate_batch(
        packet_dir / "packet_0001.json", FIXTURE_ROOT / "responses/valid_initial_response.json",
        private_assets_path=FIXTURE_ROOT / "verification_assets.jsonl", private_assets_root=FIXTURE_ROOT / "private_assets", output_path=output,
    )
    assert code == 0, result
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["candidate_id"] == "rtlgen_synthetic_example_attempt_01"
    assert "candidate_id" not in record["candidate"]
    assert "testbench" not in record["candidate"]


def test_fenced_response_and_reference_copy_are_rejected(tmp_path: Path) -> None:
    packet_dir = tmp_path / "packets"
    result, code = export_teacher_generation_packets(FIXTURE_ROOT / "generation_tasks.jsonl", packet_dir)
    assert code == 0, result
    fenced = tmp_path / "fenced.json"
    fenced.write_text("```json\n{}\n```\n", encoding="utf-8")
    result, code = validate_teacher_candidate_batch(packet_dir / "packet_0001.json", fenced)
    assert code != 0
    assert any(value in result["errors"][0].lower() for value in ("fence", "malformed"))
    copied = tmp_path / "copied.json"
    copied.write_text(json.dumps({"rows": [{"schema_version": "rtl_teacher_candidate_v0.1", "task_id": "rtlgen_synthetic_example", "top_module": "TopModule", "language": "systemverilog", "rtl": (FIXTURE_ROOT / "private_assets/workspace/rtlgen_synthetic_example/reference.sv").read_text(), "implementation_summary": "x", "assumptions": []}]}), encoding="utf-8")
    result, code = validate_teacher_candidate_batch(packet_dir / "packet_0001.json", copied, private_assets_path=FIXTURE_ROOT / "verification_assets.jsonl", private_assets_root=FIXTURE_ROOT / "private_assets")
    assert code != 0
    assert "private" in result["errors"][0].lower()
