from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "data" / "golden" / "golden_v0.1.jsonl"


@pytest.fixture
def valid_row() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8").splitlines()[0])


def write_rows(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(deepcopy(row)) + "\n" for row in rows), encoding="utf-8")
    return path

