from __future__ import annotations

import copy
import json

from scripts.dataset.rtl_generation_preparation import _task_shape_errors, export_generation_normalization_batches, validate_generation_normalized_batch
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


def test_strict_normalization_response_requires_only_rows_object(tmp_path) -> None:
    raw, normalized, assets, _ = _export_one(tmp_path)
    rows = json.loads(normalized.read_text(encoding="utf-8"))

    report, code = validate_generation_normalized_batch(
        raw,
        normalized,
        assets,
        require_response_object=True,
    )
    assert code == 1
    assert any("top-level rows" in error for error in report["errors"])

    normalized.write_text(json.dumps({"rows": rows}) + "\n", encoding="utf-8")
    report, code = validate_generation_normalized_batch(
        raw,
        normalized,
        assets,
        require_response_object=True,
    )
    assert code == 0, report

    normalized.write_text(json.dumps({"rows": rows, "provider": "local"}) + "\n", encoding="utf-8")
    report, code = validate_generation_normalized_batch(
        raw,
        normalized,
        assets,
        require_response_object=True,
    )
    assert code == 1
    assert any("only the top-level rows" in error for error in report["errors"])


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


def test_validator_rejects_every_required_nested_field_when_missing(tmp_path) -> None:
    _, normalized, _, _ = _export_one(tmp_path)
    base = json.loads(normalized.read_text(encoding="utf-8"))[0]
    for field in ("interface", "clocking", "reset", "provenance"):
        broken = copy.deepcopy(base)
        broken.pop(field)
        assert any(f"missing task field: {field}" in error for error in _task_shape_errors(broken))

    for field in ("ports",):
        broken = copy.deepcopy(base)
        broken["interface"].pop(field)
        assert any("interface must have exactly" in error for error in _task_shape_errors(broken))
    for field in ("clock_signal", "edge"):
        broken = copy.deepcopy(base)
        broken["clocking"].pop(field)
        assert any("clocking must have exactly" in error for error in _task_shape_errors(broken))
    for field in ("signal", "active_level", "synchronous"):
        broken = copy.deepcopy(base)
        broken["reset"].pop(field)
        assert any("reset must have exactly" in error for error in _task_shape_errors(broken))
    for field in ("name", "direction", "declaration", "packed_range", "width_bits", "signed", "description"):
        broken = copy.deepcopy(base)
        broken["interface"]["ports"][0].pop(field)
        assert any(f"missing interface port field: {field}" in error for error in _task_shape_errors(broken))
    for field in ("public_dataset_name", "public_dataset_url", "source_commit", "license", "original_source_id"):
        broken = copy.deepcopy(base)
        broken["provenance"].pop(field)
        assert any(f"missing provenance field: {field}" in error for error in _task_shape_errors(broken))
    for field in ("cycles", "min_cycles", "max_cycles", "throughput_cycles", "description"):
        broken = copy.deepcopy(base)
        broken["latency_contract"] = {
            "cycles": 1, "min_cycles": 1, "max_cycles": 1,
            "throughput_cycles": 1, "description": None,
        }
        broken["latency_contract"].pop(field)
        assert any(f"missing latency field: {field}" in error for error in _task_shape_errors(broken))
    broken = copy.deepcopy(base)
    broken["ambiguities"] = [{"topic": "timing"}]
    assert any("ambiguity 0 needs a statement" in error for error in _task_shape_errors(broken))


def test_validator_rejects_inconsistent_clock_reset_and_latency_contracts(tmp_path) -> None:
    _, normalized, _, _ = _export_one(tmp_path)
    task = json.loads(normalized.read_text(encoding="utf-8"))[0]
    task["clocking"]["clock_signal"] = "q"
    task["reset"] = {"signal": "q", "active_level": "high", "synchronous": False}
    task["latency_contract"] = {
        "cycles": 5, "min_cycles": 6, "max_cycles": 2,
        "throughput_cycles": 1, "description": None,
    }
    errors = _task_shape_errors(task)
    assert any("clock signal must name" in error for error in errors)
    assert any("reset signal must name" in error for error in errors)
    assert any("min_cycles" in error for error in errors)
    assert any("cycles conflicts" in error for error in errors)


def test_validator_rejects_drift_from_deterministic_width_and_reset_hints(tmp_path) -> None:
    _, normalized, _, raw = _export_one(tmp_path)
    task = json.loads(normalized.read_text(encoding="utf-8"))[0]
    raw_row = raw["rows"][0]

    raw_row["deterministic_interface_hints"][0]["width_bits"] = 2
    errors = _task_shape_errors(task, raw_row)
    assert any("port widths do not preserve" in error for error in errors)

    raw_row["deterministic_reset_hints"] = [{
        "signal": "rst",
        "active_level": "high",
        "synchronous": True,
    }]
    errors = _task_shape_errors(task, raw_row)
    assert any("reset contract does not preserve" in error for error in errors)
