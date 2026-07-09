from __future__ import annotations

import json
from pathlib import Path

from scripts.finetune.check_finetune_dataset import check_finetune_dataset
from scripts.finetune.export_canonical_finetune_dataset import export_canonical_finetune_dataset


def _row(source_id: str, split: str) -> dict:
    return {
        "id": f"teacher_distill_{source_id}",
        "dataset_version": "dataset_v0.1",
        "split": split,
        "source": "teacher_generated",
        "license": "unconfirmed_upstream_license",
        "design_family": "rtlcoder_synthetic",
        "task_family": "rtl_bug_review",
        "created_by": "prepare_teacher_distill_dataset",
        "review_status": "teacher_distilled_unreviewed",
        "approval_status": "not_approved",
        "dataset_stage": "teacher_distill_pilot",
        "dataset_name": "rtlcoder_synthetic_teacher_distill_v0_1",
        "schema_pair": "rtl_task_v0.1_to_rtl_answer_v0.1",
        "promotion_allowed": False,
        "split_seed": 42,
        "source_family": "rtlcoder_resyn27k",
        "source_id": source_id,
        "provenance": {
            "origin": "external_rtlcoder_gpt_generated_unverified",
            "public_dataset_name": "RTLCoder Resyn27k",
            "public_dataset_url": None,
            "source_commit": None,
            "notes": "fixture",
        },
        "tool_checks": {
            "parse": None,
            "lint": None,
            "simulation": None,
            "equivalence": None,
            "synthesis": None,
            "toggle": None,
            "power": None,
        },
        "messages": [
            {"role": "system", "content": "You are an RTL review specialist."},
            {
                "role": "user",
                "content": {
                    "schema_version": "rtl_task.v0.1",
                    "source_id": source_id,
                    "task_type": "rtl_bug_review",
                    "prompt": f"Review {source_id}.",
                },
            },
            {
                "role": "assistant",
                "content": {
                    "schema_version": "rtl_answer.v0.1",
                    "source_id": source_id,
                    "task_type": "rtl_bug_review",
                    "issue_summary": [],
                },
            },
        ],
    }


def _write_dataset_dir(tmp_path: Path, *, rows_per_split: int = 2, dataset_dir: Path | None = None) -> Path:
    dataset_dir = dataset_dir or (tmp_path / "distill_dataset")
    dataset_dir.mkdir(parents=True)
    for split_name in ("train", "validation", "test"):
        rows = [_row(f"{split_name}_{index}", split_name) for index in range(rows_per_split)]
        path = dataset_dir / f"{split_name}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return dataset_dir


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_exports_canonical_schema_versions_and_preserves_input(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    before = {
        path.name: path.read_text(encoding="utf-8")
        for path in dataset_dir.glob("*.jsonl")
    }
    source_summary, source_code = check_finetune_dataset(dataset_dir)
    output_dir = tmp_path / "canonical_dataset"

    summary, code = export_canonical_finetune_dataset(dataset_dir, output_dir)

    assert source_code == 0, source_summary
    assert source_summary["schema_aliases"]["user"] == {"rtl_task.v0.1": 6}
    assert source_summary["schema_aliases"]["assistant"] == {"rtl_answer.v0.1": 6}
    assert code == 0, summary
    assert summary["normalized_alias_counts"]["user"] == {"rtl_task.v0.1": 6}
    assert summary["normalized_alias_counts"]["assistant"] == {"rtl_answer.v0.1": 6}
    assert summary["changed_rows"] == 6

    for split_name in ("train", "validation", "test"):
        for row in _read_jsonl(output_dir / f"{split_name}.jsonl"):
            assert row["messages"][1]["content"]["schema_version"] == "rtl_task_v0.1"
            assert row["messages"][2]["content"]["schema_version"] == "rtl_answer_v0.1"

    after = {
        path.name: path.read_text(encoding="utf-8")
        for path in dataset_dir.glob("*.jsonl")
    }
    assert before == after

    exported_summary, exported_code = check_finetune_dataset(output_dir)
    assert exported_code == 0, exported_summary
    assert exported_summary["schema_aliases"] == {"user": {}, "assistant": {}}


def test_manifest_omits_self_hash_and_keeps_split_hashes(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    output_dir = tmp_path / "canonical_dataset"

    summary, code = export_canonical_finetune_dataset(dataset_dir, output_dir)

    assert code == 0, summary
    manifest_path = output_dir / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["output_files"]["manifest"]["sha256"] is None
    assert "self-hash is omitted" in manifest["output_files"]["manifest"]["note"].lower()
    for split_name in ("train", "validation", "test"):
        assert manifest["output_files"][split_name]["sha256"]


def test_fails_missing_split_file_without_writing_outputs(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    (dataset_dir / "validation.jsonl").unlink()
    output_dir = tmp_path / "canonical_dataset"

    summary, code = export_canonical_finetune_dataset(dataset_dir, output_dir)

    assert code == 1
    assert any("missing split file" in error for error in summary["errors"])
    assert not output_dir.exists()


def test_rejects_approved_rows_outside_golden_dataset(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    train_rows = _read_jsonl(dataset_dir / "train.jsonl")
    train_rows[0]["approval_status"] = "approved"
    (dataset_dir / "train.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in train_rows),
        encoding="utf-8",
    )
    output_dir = tmp_path / "canonical_dataset"

    summary, code = export_canonical_finetune_dataset(dataset_dir, output_dir)

    assert code == 1
    assert any("approved outside a golden dataset" in error for error in summary["errors"])
    assert not output_dir.exists()


def test_allows_approved_rows_only_when_input_dataset_is_under_golden_path(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(
        tmp_path,
        dataset_dir=tmp_path / "data" / "golden" / "fixture_dataset",
    )
    train_rows = _read_jsonl(dataset_dir / "train.jsonl")
    train_rows[0]["approval_status"] = "approved"
    (dataset_dir / "train.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in train_rows),
        encoding="utf-8",
    )
    output_dir = tmp_path / "canonical_dataset"

    summary, code = export_canonical_finetune_dataset(dataset_dir, output_dir)

    assert code == 0, summary
    assert (output_dir / "train.jsonl").is_file()
    exported_rows = _read_jsonl(output_dir / "train.jsonl")
    assert exported_rows[0]["messages"][1]["content"]["schema_version"] == "rtl_task_v0.1"
    assert exported_rows[0]["messages"][2]["content"]["schema_version"] == "rtl_answer_v0.1"


def test_refuses_overwrite_without_force(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    output_dir = tmp_path / "canonical_dataset"
    first, first_code = export_canonical_finetune_dataset(dataset_dir, output_dir)
    second, second_code = export_canonical_finetune_dataset(dataset_dir, output_dir)

    assert first_code == 0, first
    assert second_code == 1
    assert any("rerun with --force" in error for error in second["errors"])


def test_force_overwrites_managed_files_and_preserves_unknown_files(tmp_path) -> None:
    dataset_dir = _write_dataset_dir(tmp_path)
    output_dir = tmp_path / "canonical_dataset"
    first, first_code = export_canonical_finetune_dataset(dataset_dir, output_dir)
    assert first_code == 0, first

    note_path = output_dir / "keep_me.txt"
    note_path.write_text("preserve me\n", encoding="utf-8")

    train_rows = _read_jsonl(dataset_dir / "train.jsonl")
    train_rows[0]["messages"][1]["content"]["prompt"] = "Updated prompt."
    (dataset_dir / "train.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in train_rows),
        encoding="utf-8",
    )

    second, second_code = export_canonical_finetune_dataset(dataset_dir, output_dir, force=True)

    assert second_code == 0, second
    assert note_path.read_text(encoding="utf-8") == "preserve me\n"
    exported_rows = _read_jsonl(output_dir / "train.jsonl")
    assert exported_rows[0]["messages"][1]["content"]["prompt"] == "Updated prompt."
    assert (output_dir / "manifest.json").is_file()
