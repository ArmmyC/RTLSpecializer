from __future__ import annotations

import json
import subprocess
import sys

from scripts.dataset.inspect_dataset import inspect_dataset
from tests.dataset.conftest import GOLDEN, ROOT, write_rows


def test_inspect_returns_expected_counts() -> None:
    result, errors = inspect_dataset(GOLDEN)
    assert not errors
    assert result["rows"] == 20
    assert result["by_split"] == {"unsplit": 20}
    assert result["by_task_type"]["rtl_bug_review"] == 5
    assert len(result["by_design_family"]) == 10
    assert result["duplicate_ids"] == []


def test_inspect_json_cli(tmp_path) -> None:
    command = [sys.executable, str(ROOT / "scripts/dataset/inspect_dataset.py"), "--input", str(GOLDEN), "--json"]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["rows"] == 20


def test_inspect_flags_duplicate_ids(tmp_path, valid_row) -> None:
    path = write_rows(tmp_path / "rows.jsonl", [valid_row, valid_row])
    result, errors = inspect_dataset(path)
    assert not errors
    assert result["duplicate_ids"] == [valid_row["id"]]
