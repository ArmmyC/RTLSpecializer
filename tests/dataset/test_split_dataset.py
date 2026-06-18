from __future__ import annotations

import json
import subprocess
import sys

from scripts.dataset.validation import validate_dataset_file
from tests.dataset.conftest import GOLDEN, ROOT, write_rows


def test_split_command_creates_valid_isolated_files(tmp_path) -> None:
    command = [sys.executable, str(ROOT / "scripts/dataset/split_dataset.py"), "--input", str(GOLDEN), "--output-dir", str(tmp_path), "--seed", "7"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    families = {}
    total = 0
    for split in ("train", "val", "test"):
        path = tmp_path / f"{split}.jsonl"
        assert path.exists()
        report = validate_dataset_file(path)
        assert report.ok, [item.format() for item in report.errors]
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line); total += 1
            assert row["split"] == split
            assert families.setdefault(row["design_family"], split) == split
    assert total == 20
    assert (tmp_path / "split_summary.json").exists()


def test_invalid_split_ratios_fail(tmp_path) -> None:
    command = [sys.executable, str(ROOT / "scripts/dataset/split_dataset.py"), "--input", str(GOLDEN), "--output-dir", str(tmp_path), "--train-ratio", ".7", "--val-ratio", ".2", "--test-ratio", ".11"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 1
    assert "sum to 1.0" in completed.stdout


def test_split_rejects_unsplit_draft_without_override(tmp_path, valid_row) -> None:
    valid_row["review_status"] = "draft"
    input_path = write_rows(tmp_path / "draft.jsonl", [valid_row])
    output_path = tmp_path / "output"
    command = [sys.executable, str(ROOT / "scripts/dataset/split_dataset.py"), "--input", str(input_path), "--output-dir", str(output_path)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 1
    assert "--allow-unreviewed" in completed.stdout
