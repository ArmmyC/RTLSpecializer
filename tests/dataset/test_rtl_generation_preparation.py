from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from scripts.dataset.rtl_generation_preparation import SourceRow, _task_id, export_generation_normalization_batches
from tests.dataset.rtl_generation_test_helpers import load_batch, make_checkout


def test_manual_normalization_prompt_matches_generation_task_schema() -> None:
    prompt_path = Path(__file__).resolve().parents[2] / "docs/dataset/llm_rtl_generation_task_normalization_prompt.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    for field in (
        "schema_version", "task_id", "source_id", "source_dataset", "design_family",
        "language", "specification", "top_module", "interface", "clocking", "reset",
        "latency_contract", "behavioral_constraints", "assumptions", "ambiguities", "provenance",
    ):
        assert f"`{field}`" in prompt or f'"{field}"' in prompt, field
    for field in ("name", "direction", "declaration", "packed_range", "width_bits", "signed", "description"):
        assert f"`{field}`" in prompt, field
    collapsed = " ".join(prompt.split())
    assert "exactly `clock_signal` and `edge`" in collapsed
    assert "exactly `signal`, `active_level`, and `synchronous`" in collapsed
    for field in ("cycles", "min_cycles", "max_cycles", "throughput_cycles", "description"):
        assert f"`{field}`" in prompt, field
    assert "do not include a top-level `license`" in prompt
    assert "provenance.license" in prompt
    assert "additionalProperties: false" in prompt
    for forbidden in (
        "`raw_specification`", "deterministic hint fields", "reference RTL",
        "testbench content", "private paths", "tool results", "expected vectors",
        "generated RTL",
    ):
        assert forbidden in prompt, forbidden
    assert not re.search(r"\b(?:return|include|output|set|emit)\s+(?:the\s+)?[`\"]constraints[`\"]", prompt, re.IGNORECASE)
    assert '"constraints":' not in prompt


def test_export_separates_public_and_private_bytes_and_is_deterministic(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=3)
    first_public = tmp_path / "public-one"
    first_private = tmp_path / "private-one"
    second_public = tmp_path / "public-two"
    second_private = tmp_path / "private-two"
    first, first_code = export_generation_normalization_batches(source, first_public, first_private, batch_size=2)
    second, second_code = export_generation_normalization_batches(source, second_public, second_private, batch_size=2)
    assert first_code == second_code == 0, (first, second)
    first_payload = load_batch(first_public / "batch_001.json")
    second_payload = load_batch(second_public / "batch_001.json")
    assert first_payload == second_payload
    public_text = (first_public / "batch_001.json").read_text(encoding="utf-8")
    assert "input" not in first_payload
    assert first_payload["source_label"] == "VerilogEval"
    assert "raw_reference_rtl" not in public_text
    assert "raw_testbench" not in public_text
    assert "reference.sv" not in public_text
    row = first_payload["rows"][0]
    assert row["raw_specification"].startswith("Implement module named TopModule")
    assets = [json.loads(line) for line in (first_private / "verification_assets.jsonl").read_text().splitlines() if line.strip()]
    assert len(assets) == 3
    asset = assets[0]
    assert asset["input_hashes"]["reference_rtl_sha256"]
    assert (first_private / asset["reference_rtl_path"]).is_file()
    assert (first_private / asset["testbench_path"]).is_file()
    assert (first_private / asset["reference_rtl_path"]).read_text().startswith("module RefModule")


def test_public_batch_does_not_leak_local_checkout_or_private_paths(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    leaked_source = tmp_path / "data" / ".local_data" / "verilog-eval-main" / "dataset_spec-to-rtl"
    leaked_source.parent.mkdir(parents=True)
    source.rename(leaked_source)
    private = tmp_path / "data" / ".local_data" / "private-verification-output"
    public = tmp_path / "public"
    result, code = export_generation_normalization_batches(leaked_source, public, private)
    assert code == 0, result
    payload = load_batch(public / "batch_001.json")
    text = (public / "batch_001.json").read_text(encoding="utf-8")
    for forbidden in (".local_data", "verilog-eval-main", "dataset_spec-to-rtl", str(leaked_source), str(private), "reference RTL", "testbench text"):
        assert forbidden not in text
    assert payload["source_label"] == "VerilogEval"


def test_task_id_uses_only_canonical_identity_fields() -> None:
    base = SourceRow(
        source_id="source-1", source_dataset="VerilogEval", design_family="sequential",
        specification="prompt", reference_rtl="rtl", testbench="tb", license="MIT",
        provenance={"notes": "one", "public_dataset_url": "https://example.invalid/one"},
        source_path="data/.local_data/first", source_commit="abc123",
    )
    assert _task_id(base) == _task_id(replace(base, provenance={"notes": "changed"}, source_path="/other/private/path"))
    assert _task_id(base) != _task_id(replace(base, source_commit="def456"))
    assert _task_id(base) != _task_id(replace(base, source_id="source-2"))
    assert _task_id(base) != _task_id(replace(base, source_dataset="OtherDataset"))


def test_export_limit_and_support_files_are_preserved(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=4, with_support=True)
    result, code = export_generation_normalization_batches(source, tmp_path / "public", tmp_path / "private", batch_size=5, limit=2)
    assert code == 0, result
    assert result["exported_rows"] == 2
    assets = [json.loads(line) for line in (tmp_path / "private" / "verification_assets.jsonl").read_text().splitlines() if line.strip()]
    assert len(assets) == 2
    assert assets[0]["support_files"]
    support_path = tmp_path / "private" / assets[0]["support_files"][0]
    assert support_path.read_text(encoding="utf-8").startswith("module TopModule")


def test_duplicate_source_ids_and_missing_prompt_fail_before_output(tmp_path) -> None:
    duplicate = [
        {"source_id": "same", "source_dataset": "fixture", "prompt": "a", "artifacts": {"rtl_code": "module A; endmodule"}},
        {"source_id": "same", "source_dataset": "fixture", "prompt": "b", "artifacts": {"rtl_code": "module B; endmodule"}},
    ]
    source = tmp_path / "rows.json"
    source.write_text(json.dumps(duplicate) + "\n", encoding="utf-8")
    result, code = export_generation_normalization_batches(source, tmp_path / "public", tmp_path / "private")
    assert code == 1
    assert any("duplicate source_id" in error for error in result["errors"])
    assert not (tmp_path / "public" / "batch_001.json").exists()
    missing = [{"source_id": "missing", "source_dataset": "fixture", "artifacts": {"rtl_code": "module A; endmodule"}}]
    missing_path = tmp_path / "missing.json"
    missing_path.write_text(json.dumps(missing) + "\n", encoding="utf-8")
    result, code = export_generation_normalization_batches(missing_path, tmp_path / "public-missing", tmp_path / "private-missing")
    assert code == 1
    assert any("missing specification" in error for error in result["errors"])
