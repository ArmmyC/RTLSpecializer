from __future__ import annotations

import json

from scripts.dataset.rtl_generation_preparation import export_generation_normalization_batches
from tests.dataset.rtl_generation_test_helpers import load_batch, make_checkout


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
