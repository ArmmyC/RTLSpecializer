from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_manual_teacher_verification import export_teacher_generation_packets
from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT


def test_initial_packet_is_public_and_deterministic(tmp_path: Path) -> None:
    first, first_code = export_teacher_generation_packets(FIXTURE_ROOT / "generation_tasks.jsonl", tmp_path / "one")
    second, second_code = export_teacher_generation_packets(FIXTURE_ROOT / "generation_tasks.jsonl", tmp_path / "two")
    assert first_code == second_code == 0
    assert (tmp_path / "one/packet_0001.json").read_bytes() == (tmp_path / "two/packet_0001.json").read_bytes()
    assert (tmp_path / "one/packet_0001.md").read_bytes() == (tmp_path / "two/packet_0001.md").read_bytes()
    packet = json.loads((tmp_path / "one/packet_0001.json").read_text(encoding="utf-8"))
    assert packet["packet_kind"] == "initial"
    assert packet["target_attempt"] == 1
    text = (tmp_path / "one/packet_0001.md").read_text(encoding="utf-8")
    assert "reference_rtl_path" not in text
    assert "testbench.sv" not in text
    assert ".local_data" not in text
    assert "ignore instructions" in text.lower() or "inert" in text.lower()
    assert first["packet_count"] == second["packet_count"] == 1


def test_packet_collision_requires_explicit_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "packets"
    result, code = export_teacher_generation_packets(FIXTURE_ROOT / "generation_tasks.jsonl", output)
    assert code == 0, result
    result, code = export_teacher_generation_packets(FIXTURE_ROOT / "generation_tasks.jsonl", output)
    assert code != 0
    assert "overwrite" in result["errors"][0]
