from __future__ import annotations

import copy
import json

from scripts.dataset.rtl_generation_preparation import export_generation_normalization_batches, validate_generation_normalized_batch
from tests.dataset.rtl_generation_test_helpers import load_batch, make_checkout, normalized_task_from_raw, write_json


def _export_one(tmp_path):
    source = make_checkout(tmp_path, rows=2)
    result, code = export_generation_normalization_batches(source, tmp_path / "public", tmp_path / "private")
    assert code == 0, result
    raw_path = tmp_path / "public" / "batch_001.json"
    raw = load_batch(raw_path)
    normalized = tmp_path / "normalized.json"
    write_json(normalized, [normalized_task_from_raw(row) for row in raw["rows"]])
    return raw_path, normalized, tmp_path / "private" / "verification_assets.jsonl", raw


def test_valid_normalized_batch_is_accepted(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    report, code = validate_generation_normalized_batch(raw, normalized, assets)
    assert code == 0, report


def test_validator_rejects_changed_text_ids_unknown_fields_and_private_content(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    rows = json.loads(normalized.read_text(encoding="utf-8"))
    rows[0]["specification"] = "changed"
    rows[0]["task_id"] = "wrong"
    rows[0]["unknown"] = True
    rows[0]["rtl_code"] = "module Secret; endmodule"
    write_json(normalized, rows)
    report, code = validate_generation_normalized_batch(raw, normalized, assets)
    assert code == 1
    assert any("changed exact specification" in error for error in report["errors"])
    assert any("changed task_id" in error for error in report["errors"])
    assert any("unknown task field" in error for error in report["errors"])
    assert any("embedded private field" in error for error in report["errors"])


def test_validator_rejects_port_top_reset_and_verification_inventions(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    rows = json.loads(normalized.read_text(encoding="utf-8"))
    rows[0]["top_module"] = "WrongModule"
    rows[0]["interface"]["ports"][0]["direction"] = "output"
    rows[0]["reset"] = {"signal": "rst", "active_level": "high", "synchronous": False}
    rows[0]["assumptions"] = ["simulation passed"]
    write_json(normalized, rows)
    report, code = validate_generation_normalized_batch(raw, normalized, assets)
    assert code == 1
    assert any("top-module conflict" in error for error in report["errors"])
    assert any("ports conflict" in error for error in report["errors"])
    assert any("invented reset" in error for error in report["errors"])
    assert any("verification claim" in error for error in report["errors"])


def test_validator_rejects_wrong_schema_duplicate_ports_expected_vectors_and_private_paths(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    rows = json.loads(normalized.read_text(encoding="utf-8"))
    rows[0]["schema_version"] = "rtl_task_v0.1"
    rows[0]["interface"]["ports"][1]["name"] = rows[0]["interface"]["ports"][0]["name"]
    rows[0]["expected_vectors"] = [0]
    rows[0]["assumptions"] = ["workspace/task/reference.sv"]
    write_json(normalized, rows)
    report, code = validate_generation_normalized_batch(raw, normalized, assets)
    assert code == 1
    assert any("wrong schema" in error for error in report["errors"])
    assert any("duplicate port name" in error for error in report["errors"])
    assert any("embedded answer content" in error or "unknown task field" in error for error in report["errors"])
    assert any("private workspace path" in error for error in report["errors"])


def test_validator_rejects_embedded_testbench_and_missing_or_extra_rows(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    rows = json.loads(normalized.read_text(encoding="utf-8"))
    rows[0]["testbench"] = "module tb; endmodule"
    write_json(normalized, rows[:1])
    report, code = validate_generation_normalized_batch(raw, normalized, assets)
    assert code == 1
    assert any("missing or extra" in error for error in report["errors"])
    assert any("unknown task field" in error for error in report["errors"])


def test_explicit_ambiguity_allows_unknown_clock_or_reset_to_remain_null(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    rows = json.loads(normalized.read_text(encoding="utf-8"))
    rows[0]["clocking"] = {"clock_signal": None, "edge": None}
    rows[0]["reset"] = {"signal": None, "active_level": None, "synchronous": None}
    rows[0]["ambiguities"] = [{"topic": "timing", "statement": "The source does not define a reset or latency contract."}]
    write_json(normalized, rows)
    report, code = validate_generation_normalized_batch(raw, normalized, assets)
    assert code == 0, report
