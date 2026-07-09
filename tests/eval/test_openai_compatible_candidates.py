from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.eval.openai_compatible_candidate_runner as runner
from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.eval.evaluator import evaluate_answer
from scripts.eval.make_baseline_candidates import make_baseline_answer
from scripts.eval.openai_compatible_candidate_runner import (
    DEFAULT_API_KEY_ENV,
    OpenAICompatibleChatClient,
    OpenAICompatibleRunnerConfig,
    build_request_messages,
    load_schema_reminder_text,
    parse_candidate_answer_text,
    run_openai_compatible_candidates,
)
from tests.dataset.conftest import GOLDEN, ROOT


class CapturingClient:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return next(self.outputs)


class FailingClient:
    def __init__(self, error: str = "fixture endpoint unavailable"):
        self.error = error
        self.calls = 0

    def complete(self, **kwargs) -> str:
        self.calls += 1
        raise RuntimeError(self.error)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch) -> None:
    monkeypatch.setenv(DEFAULT_API_KEY_ENV, "fixture-secret")


@pytest.fixture
def rows() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()[:2]]


def _dataset(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "dataset.jsonl"
    write_jsonl(path, rows)
    return path


def _config(tmp_path: Path, rows: list[dict], **changes) -> OpenAICompatibleRunnerConfig:
    values = {
        "dataset": _dataset(tmp_path, rows),
        "output": tmp_path / "candidates.jsonl",
        "base_url": "http://127.0.0.1:8000/v1",
    }
    values.update(changes)
    return OpenAICompatibleRunnerConfig(**values)


def test_prompt_excludes_reference_answer_and_uses_only_system_and_user_messages(rows) -> None:
    row = deepcopy(rows[0])
    marker = "UNIQUE_REFERENCE_ONLY_MARKER"
    row["messages"][2]["content"]["limitations"] = [marker]
    messages = build_request_messages(row)
    assert [message["role"] for message in messages] == ["system", "user"]
    combined = "\n".join(message["content"] for message in messages)
    assert marker not in combined


def test_schema_reminder_is_appended_to_system_prompt(tmp_path, rows) -> None:
    reminder = "RETURN THE EXACT RTL ANSWER SCHEMA."
    client = CapturingClient([json.dumps(rows[0]["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(
        _config(tmp_path, rows[:1], schema_reminder=reminder),
        client,
    )
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert code == 0 and summary["ok"]
    assert reminder in system_prompt


def test_schema_reminder_does_not_include_reference_assistant_content(tmp_path, rows) -> None:
    row = deepcopy(rows[0])
    marker = "UNIQUE_REFERENCE_ONLY_MARKER"
    row["messages"][2]["content"]["limitations"] = [marker]
    reminder = "RETURN THE EXACT RTL ANSWER SCHEMA."
    client = CapturingClient([json.dumps(row["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(
        _config(tmp_path, [row], schema_reminder=reminder),
        client,
    )
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert code == 0 and summary["ok"]
    assert reminder in system_prompt
    assert marker not in system_prompt


def test_schema_reminder_file_loads_correctly(tmp_path, rows) -> None:
    reminder_file = tmp_path / "schema_reminder.md"
    reminder_text = "Return the exact rtl_answer_v0.1 object."
    reminder_file.write_text(reminder_text, encoding="utf-8")
    client = CapturingClient([json.dumps(rows[0]["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(
        _config(tmp_path, rows[:1], schema_reminder_file=reminder_file),
        client,
    )
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert code == 0 and summary["ok"]
    assert reminder_text in system_prompt
    assert summary["schema_reminder_enabled"] is True
    assert load_schema_reminder_text(schema_reminder_file=reminder_file) == reminder_text


def test_request_uses_only_system_and_user_messages(tmp_path, rows) -> None:
    client = CapturingClient([json.dumps(rows[0]["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(_config(tmp_path, rows[:1]), client)
    request_messages = client.calls[0]["messages"]
    assert code == 0 and summary["ok"]
    assert len(request_messages) == 2
    assert request_messages[0]["role"] == "system"
    assert request_messages[1]["role"] == "user"


def test_direct_json_parse_works() -> None:
    answer, parse_error = parse_candidate_answer_text('{"schema_version":"rtl_answer_v0.1"}')
    assert parse_error is None
    assert answer == {"schema_version": "rtl_answer_v0.1"}


def test_markdown_fenced_json_parse_works() -> None:
    answer, parse_error = parse_candidate_answer_text("```json\n{\"schema_version\":\"rtl_answer_v0.1\"}\n```")
    assert parse_error is None
    assert answer["schema_version"] == "rtl_answer_v0.1"


def test_text_surrounded_json_parse_works() -> None:
    answer, parse_error = parse_candidate_answer_text('response: {"schema_version":"rtl_answer_v0.1"} thanks')
    assert parse_error is None
    assert answer["schema_version"] == "rtl_answer_v0.1"


class FakeHTTPResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_response_format_json_adds_payload(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(http_request, timeout):
        seen["payload"] = json.loads(http_request.data.decode("utf-8"))
        return FakeHTTPResponse('{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')

    monkeypatch.setattr(runner.request, "urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient("http://127.0.0.1:8000/v1", "fixture-secret")
    client.complete(
        model="fixture-model",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=32,
        timeout=5.0,
        response_format_json=True,
    )
    assert seen["payload"]["response_format"] == {"type": "json_object"}


def test_no_response_format_is_sent_without_flag(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(http_request, timeout):
        seen["payload"] = json.loads(http_request.data.decode("utf-8"))
        return FakeHTTPResponse('{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')

    monkeypatch.setattr(runner.request, "urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient("http://127.0.0.1:8000/v1", "fixture-secret")
    client.complete(
        model="fixture-model",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        max_tokens=32,
        timeout=5.0,
        response_format_json=False,
    )
    assert "response_format" not in seen["payload"]


def test_parse_failure_still_writes_candidate_row(tmp_path, rows) -> None:
    client = CapturingClient(["not json at all"])
    config = _config(tmp_path, rows[:1])
    summary, code = run_openai_compatible_candidates(config, client)
    loaded, problems = load_jsonl(config.output)
    candidate = loaded[0][1]
    assert code == 0 and summary["ok"] and not problems
    assert summary["parse_error_rows"] == 1
    assert candidate["answer"]["schema_version"] == "parse_error"
    assert candidate["metadata"]["parse_error"] == "model output did not contain a JSON object"


def test_api_failure_writes_api_error_candidate_when_not_fail_fast(tmp_path, rows) -> None:
    client = FailingClient()
    config = _config(tmp_path, rows[:1], retries=1)
    summary, code = run_openai_compatible_candidates(config, client)
    loaded, problems = load_jsonl(config.output)
    candidate = loaded[0][1]
    assert code == 0 and summary["ok"] and not problems
    assert client.calls == 2
    assert summary["api_error_rows"] == 1
    assert candidate["answer"]["schema_version"] == "api_error"
    assert candidate["answer"]["raw_text"] is None


def test_resume_skips_existing_ids(tmp_path, rows) -> None:
    config = _config(tmp_path, rows, resume=True)
    existing = {
        "id": rows[0]["id"],
        "answer": deepcopy(rows[0]["messages"][2]["content"]),
        "metadata": {"model": "existing"},
    }
    write_jsonl(config.output, [existing])
    client = CapturingClient([json.dumps(rows[1]["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(config, client)
    loaded, problems = load_jsonl(config.output)
    assert code == 0 and summary["ok"] and not problems
    assert summary["skipped_rows"] == 1
    assert summary["written_rows"] == 1
    assert [row["id"] for _, row in loaded] == [rows[0]["id"], rows[1]["id"]]


def test_duplicate_candidate_ids_are_not_produced(tmp_path, rows) -> None:
    config = _config(tmp_path, rows, resume=True)
    existing = {
        "id": rows[0]["id"],
        "answer": deepcopy(rows[0]["messages"][2]["content"]),
        "metadata": {"model": "existing"},
    }
    write_jsonl(config.output, [existing])
    client = CapturingClient([json.dumps(rows[1]["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(config, client)
    loaded, problems = load_jsonl(config.output)
    ids = [row["id"] for _, row in loaded]
    assert code == 0 and summary["ok"] and not problems
    assert ids == [rows[0]["id"], rows[1]["id"]]
    assert len(ids) == len(set(ids))


def test_api_key_is_not_written_to_metadata_or_raw_output(tmp_path, rows, monkeypatch) -> None:
    secret = "unique-fixture-secret-value"
    monkeypatch.setenv(DEFAULT_API_KEY_ENV, secret)
    config = _config(tmp_path, rows[:1], raw_output_dir=tmp_path / "raw")
    client = CapturingClient([json.dumps(rows[0]["messages"][2]["content"])])
    summary, code = run_openai_compatible_candidates(config, client)
    output_text = config.output.read_text(encoding="utf-8")
    raw_text = next(config.raw_output_dir.iterdir()).read_text(encoding="utf-8")
    assert code == 0 and summary["ok"]
    assert secret not in output_text
    assert secret not in raw_text
    assert secret not in json.dumps(summary)


def test_smoke_like_malformed_schema_candidate_still_evaluates_low(rows) -> None:
    answer = {
        "issue_summary": [{"issue": "Possible issue", "severity": "low", "evidence": {"reason": "short"}}],
        "claim_levels": {
            "correctness": "high",
            "area": "low",
            "activity": "low",
            "power": "low",
        },
    }
    result = evaluate_answer(rows[0], answer)
    assert result.score <= 0.25
    assert any("missing answer fields" in error for error in result.errors)
    assert any("invalid claim_levels" in error for error in result.errors)


def test_well_shaped_candidate_with_required_fields_evaluates_without_schema_errors(rows) -> None:
    answer = make_baseline_answer(rows[0])
    result = evaluate_answer(rows[0], answer)
    assert not any("missing answer fields" in error for error in result.errors), result.errors
    assert not any("invalid claim_levels" in error for error in result.errors), result.errors
    assert not any("patch must be an object" in error for error in result.errors), result.errors


def test_cli_json_output_is_parseable(tmp_path) -> None:
    output = tmp_path / "candidates.jsonl"
    env = dict(os.environ)
    env.pop(DEFAULT_API_KEY_ENV, None)
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/eval/run_openai_compatible_candidates.py",
            "--dataset",
            str(GOLDEN),
            "--output",
            str(output),
            "--base-url",
            "http://127.0.0.1:8000/v1",
            "--limit",
            "1",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["errors"]
