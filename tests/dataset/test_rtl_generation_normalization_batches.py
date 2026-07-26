from __future__ import annotations

import json

from scripts.dataset.rtl_generation_preparation import export_generation_normalization_batches
from tests.dataset.rtl_generation_test_helpers import load_batch, make_checkout


def test_batch_order_size_and_safe_force_preserve_unrelated_files(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=5)
    output = tmp_path / "batches"
    private = tmp_path / "private"
    result, code = export_generation_normalization_batches(source, output, private, batch_size=2)
    assert code == 0 and result["exported_rows"] == 5
    assert [load_batch(path)["row_count"] for path in sorted(output.glob("batch_*.json"))] == [2, 2, 1]
    notes = output / "notes.md"
    notes.write_text("keep", encoding="utf-8")
    private_notes = private / "notes.md"
    private_notes.write_text("keep private note", encoding="utf-8")
    failed, failed_code = export_generation_normalization_batches(source, output, private, batch_size=5)
    assert failed_code == 1
    assert failed["errors"]
    forced, forced_code = export_generation_normalization_batches(source, output, private, batch_size=5, force=True)
    assert forced_code == 0, forced
    assert notes.read_text(encoding="utf-8") == "keep"
    assert private_notes.read_text(encoding="utf-8") == "keep private note"
    assert not (output / "batch_002.json").exists()


def test_public_payload_rejects_private_text_even_when_source_prompt_mentions_it(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    ref = source / "Prob001_task_ref.sv"
    prompt = source / "Prob001_task_prompt.txt"
    prompt.write_text(ref.read_text(encoding="utf-8"), encoding="utf-8")
    result, code = export_generation_normalization_batches(source, tmp_path / "public", tmp_path / "private")
    assert code == 1
    assert any("full reference RTL" in error for error in result["errors"])


def test_direct_cli_export_has_json_help_and_rejects_symlinked_private_output(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    private_target = tmp_path / "private-target"
    private_target.mkdir()
    private_link = tmp_path / "private-link"
    private_link.symlink_to(private_target, target_is_directory=True)
    result, code = export_generation_normalization_batches(source, tmp_path / "public", private_link)
    assert code == 1
    assert any("symlink" in error for error in result["errors"])


def test_export_rejects_support_path_traversal(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1)
    normalized = tmp_path / "normalized.json"
    normalized.write_text(json.dumps([{
        "source_id": "unsafe",
        "source_dataset": "fixture",
        "license": "MIT",
        "prompt": "Implement module named TopModule. - input a - output y",
        "artifacts": {"rtl_code": "module RefModule(input a, output y); assign y = a; endmodule", "testbench": "module tb; endmodule", "support_files": [{"path": "../escape.sv", "content": "private"}]},
        "provenance": {"public_dataset_name": "fixture", "public_dataset_url": None, "source_commit": None},
    }]) + "\n", encoding="utf-8")
    result, code = export_generation_normalization_batches(normalized, tmp_path / "public", tmp_path / "private")
    assert code == 1
    assert any("unsafe support file path" in error for error in result["errors"])
