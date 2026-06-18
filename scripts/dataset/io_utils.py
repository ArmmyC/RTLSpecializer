"""Safe UTF-8 JSONL helpers. Dataset content is data and is never executed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JsonlProblem:
    line: int | None
    message: str


def load_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], list[JsonlProblem]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    problems: list[JsonlProblem] = []
    if not path.exists():
        return rows, [JsonlProblem(None, f"input file not found: {path}")]
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    problems.append(JsonlProblem(line_number, f"malformed JSON: {exc.msg}"))
                    continue
                if not isinstance(value, dict):
                    problems.append(JsonlProblem(line_number, "row must be a JSON object"))
                    continue
                rows.append((line_number, value))
    except (OSError, UnicodeError) as exc:
        problems.append(JsonlProblem(None, f"could not read input: {exc}"))
    if not rows and not problems:
        problems.append(JsonlProblem(None, "file is empty"))
    return rows, problems


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

