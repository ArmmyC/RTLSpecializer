from __future__ import annotations

import json
import copy

from scripts.dataset.rtl_generation_preparation import _asset_shape_errors, assemble_generation_inputs, export_generation_normalization_batches
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
    assert any("missing private asset" in error for error in result["errors"])


def test_assembly_rejects_duplicate_private_task_ids(tmp_path) -> None:
    _, normalized, private_assets, _ = _export_one(tmp_path)
    record = json.loads(private_assets.read_text().splitlines()[0])
    duplicate_manifest = tmp_path / "duplicate-assets.jsonl"
    duplicate_manifest.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8")
    result, code = assemble_generation_inputs(normalized, duplicate_manifest, tmp_path / "tasks.jsonl", tmp_path / "assets.jsonl")
    assert code == 1
    assert any("duplicate private asset task_id" in error for error in result["errors"])


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
    assert any("symlink" in error for error in result["errors"])


def test_assembly_accepts_full_manifest_for_one_normalized_batch(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=6)
    public, private = tmp_path / "public", tmp_path / "private"
    result, code = export_generation_normalization_batches(source, public, private, batch_size=2)
    assert code == 0, result
    raw = load_batch(public / "batch_002.json")
    normalized = tmp_path / "batch_002.normalized.json"
    write_json(normalized, [normalized_task_from_raw(row) for row in raw["rows"]])
    tasks_output, assets_output = tmp_path / "assembled.tasks.jsonl", tmp_path / "assembled.assets.jsonl"
    result, code = assemble_generation_inputs(normalized, private / "verification_assets.jsonl", tasks_output, assets_output)
    assert code == 0, result
    assert result["selected_private_asset_count"] == 2
    assert result["unused_private_asset_count"] == 4
    assert len([line for line in tasks_output.read_text().splitlines() if line]) == 2
    assert len([line for line in assets_output.read_text().splitlines() if line]) == 2


def test_private_asset_paths_use_strict_cross_platform_contract(tmp_path) -> None:
    _, _, private_assets, _ = _export_one(tmp_path)
    asset = json.loads(private_assets.read_text(encoding="utf-8").splitlines()[0])
    for value in ("../secret.sv", "workspace/../secret.sv", "workspace\\task\\reference.sv", "C:\\secret.sv", "/absolute/secret.sv", "workspace//task/reference.sv"):
        broken = copy.deepcopy(asset)
        broken["reference_rtl_path"] = value
        assert _asset_shape_errors(broken), value


def test_assembly_recomputes_all_private_and_prompt_hashes(tmp_path) -> None:
    source = make_checkout(tmp_path, rows=1, with_support=True)
    public, private = tmp_path / "public", tmp_path / "private"
    result, code = export_generation_normalization_batches(source, public, private)
    assert code == 0, result
    raw = load_batch(public / "batch_001.json")
    normalized = tmp_path / "normalized.json"
    write_json(normalized, [normalized_task_from_raw(raw["rows"][0])])
    asset = json.loads((private / "verification_assets.jsonl").read_text().splitlines()[0])

    cases = [
        (private / asset["reference_rtl_path"], b"modified reference"),
        (private / asset["testbench_path"], b"modified testbench"),
        (private / asset["support_files"][0], b"modified support"),
    ]
    for index, (path, changed) in enumerate(cases):
        original = path.read_bytes()
        path.write_bytes(changed)
        result, code = assemble_generation_inputs(normalized, private / "verification_assets.jsonl", tmp_path / f"tasks-{index}.jsonl", tmp_path / f"assets-{index}.jsonl")
        assert code == 1, result
        assert any("hash mismatch" in error for error in result["errors"])
        path.write_bytes(original)

    original_spec = normalized.read_text(encoding="utf-8")
    normalized.write_text(original_spec.replace("Task 1.", "Changed Task."), encoding="utf-8")
    result, code = assemble_generation_inputs(normalized, private / "verification_assets.jsonl", tmp_path / "tasks-spec.jsonl", tmp_path / "assets-spec.jsonl")
    assert code == 1, result
    assert any("specification hash mismatch" in error for error in result["errors"])
    normalized.write_text(original_spec, encoding="utf-8")

    modified_manifest = private / "modified-manifest.jsonl"
    asset["input_hashes"]["reference_rtl_sha256"] = "0" * 64
    modified_manifest.write_text(json.dumps(asset) + "\n", encoding="utf-8")
    result, code = assemble_generation_inputs(normalized, modified_manifest, tmp_path / "tasks-hash.jsonl", tmp_path / "assets-hash.jsonl")
    assert code == 1, result
    assert any("reference RTL hash mismatch" in error for error in result["errors"])


def copy_record(record: dict, *, task_id: str) -> dict:
    copied = json.loads(json.dumps(record))
    copied["task_id"] = task_id
    return copied
