from __future__ import annotations

import json

from scripts.dataset.rtl_generation_preparation import assemble_generation_inputs
from tests.dataset.rtl_generation_test_helpers import load_batch, make_checkout, normalized_task_from_raw, write_json
from tests.dataset.test_rtl_generation_normalized_validation import _export_one


def test_assembly_is_one_to_one_deterministic_and_atomic_per_output(tmp_path) -> None:
    raw_path, normalized, private_assets, raw = _export_one(tmp_path)
    first_tasks = tmp_path / "out-one" / "generation_tasks.jsonl"
    first_assets = tmp_path / "out-one" / "verification_assets.jsonl"
    result, code = assemble_generation_inputs(normalized, private_assets, first_tasks, first_assets)
    assert code == 0, result
    second_tasks = tmp_path / "out-two" / "generation_tasks.jsonl"
    second_assets = tmp_path / "out-two" / "verification_assets.jsonl"
    result, code = assemble_generation_inputs(normalized, private_assets, second_tasks, second_assets)
    assert code == 0, result
    assert first_tasks.read_bytes() == second_tasks.read_bytes()
    assert first_assets.read_bytes() == second_assets.read_bytes()
    assert len([line for line in first_tasks.read_text().splitlines() if line]) == len(raw["rows"])


def test_assembly_rejects_missing_extra_conflicting_and_unsafe_assets(tmp_path) -> None:
    _, normalized, private_assets, _ = _export_one(tmp_path)
    records = [json.loads(line) for line in private_assets.read_text().splitlines() if line]
    records.pop()
    records.append(copy_record(records[0], task_id="extra"))
    broken_assets = tmp_path / "broken-assets.jsonl"
    broken_assets.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    result, code = assemble_generation_inputs(normalized, broken_assets, tmp_path / "tasks.jsonl", tmp_path / "assets.jsonl")
    assert code == 1
    assert any("one-to-one" in error for error in result["errors"])


def test_force_preserves_unrelated_files_and_rejects_input_output_alias(tmp_path) -> None:
    _, normalized, private_assets, _ = _export_one(tmp_path)
    output_dir = tmp_path / "out"
    tasks = output_dir / "tasks.jsonl"
    assets = output_dir / "assets.jsonl"
    result, code = assemble_generation_inputs(normalized, private_assets, tasks, assets)
    assert code == 0, result
    notes = output_dir / "notes.md"
    notes.write_text("keep", encoding="utf-8")
    result, code = assemble_generation_inputs(normalized, private_assets, tasks, assets, force=True)
    assert code == 0, result
    assert notes.read_text(encoding="utf-8") == "keep"
    result, code = assemble_generation_inputs(normalized, private_assets, normalized, assets)
    assert code == 1
    assert any("alias" in error for error in result["errors"])


def test_assembly_rejects_top_module_conflict_and_symlinked_private_asset(tmp_path) -> None:
    _, normalized, private_assets, _ = _export_one(tmp_path)
    records = [json.loads(line) for line in private_assets.read_text().splitlines() if line]
    records[0]["top_module"] = "OtherTop"
    broken = tmp_path / "conflict-assets.jsonl"
    broken.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    result, code = assemble_generation_inputs(normalized, broken, tmp_path / "tasks.jsonl", tmp_path / "assets.jsonl")
    assert code == 1
    assert any("top module conflict" in error for error in result["errors"])
    first = records[0]
    target = private_assets.parent / first["reference_rtl_path"]
    saved = target.with_name("reference-real.sv")
    target.rename(saved)
    target.symlink_to(saved.name)
    result, code = assemble_generation_inputs(normalized, private_assets, tmp_path / "symlink-tasks.jsonl", tmp_path / "symlink-assets.jsonl")
    assert code == 1
    assert any("invalid private asset path" in error for error in result["errors"])


def copy_record(record: dict, *, task_id: str) -> dict:
    copied = json.loads(json.dumps(record))
    copied["task_id"] = task_id
    return copied
