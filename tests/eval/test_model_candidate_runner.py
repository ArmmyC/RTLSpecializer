from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.eval.model_candidate_runner as runner
from scripts.dataset.io_utils import load_jsonl, write_jsonl
from scripts.eval.model_candidate_runner import (
    RunnerConfig,
    parse_model_output,
    run_model_candidates,
    safe_raw_output_path,
    validate_endpoint,
)
from scripts.eval.model_prompting import build_prompt
from tests.dataset.conftest import GOLDEN, ROOT


class FakeClient:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)
        self.calls = 0

    def complete(self, **kwargs) -> str:
        self.calls += 1
        return next(self.outputs)


class FailingClient:
    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs) -> str:
        self.calls += 1
        raise RuntimeError("fixture endpoint unavailable")


@pytest.fixture
def rows() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines()[:2]]


def _dataset(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "input" / "dataset.jsonl"
    write_jsonl(path, rows)
    return path


def _config(tmp_path: Path, rows: list[dict], **changes) -> RunnerConfig:
    values = {
        "dataset": _dataset(tmp_path, rows),
        "output": tmp_path / "output" / "candidates.jsonl",
        "model": "fixture-model",
        "overwrite": True,
    }
    values.update(changes)
    return RunnerConfig(**values)


def test_prompt_includes_context_schema_and_claim_policy(rows) -> None:
    row = deepcopy(rows[0])
    row["messages"][2]["content"]["issue_summary"] = ["UNIQUE_REFERENCE_ONLY_MARKER"]
    messages = build_prompt(row)
    prompt = "\n".join(message["content"] for message in messages)
    assert row["task_family"] in prompt
    assert "artifacts" in prompt and "tool_checks" in prompt
    assert "rtl_answer_v0.1" in prompt
    assert "insufficient_evidence" in prompt
    assert "lint/compile" in prompt
    assert "reference answer" not in prompt.lower()
    assert "UNIQUE_REFERENCE_ONLY_MARKER" not in prompt


def test_parser_accepts_direct_json_object() -> None:
    result = parse_model_output('{"schema_version":"rtl_answer_v0.1"}')
    assert result.status == "parsed_json"
    assert result.answer == {"schema_version": "rtl_answer_v0.1"}


def test_parser_extracts_json_from_surrounding_text() -> None:
    result = parse_model_output('result follows: {"schema_version":"rtl_answer_v0.1"} done')
    assert result.status == "extracted_json"
    assert result.answer is not None


@pytest.mark.parametrize("wrapped", [False, True])
def test_parser_rejects_full_candidate_rows(wrapped) -> None:
    raw = json.dumps({"id": "row", "answer": {"schema_version": "rtl_answer_v0.1"}})
    if wrapped:
        raw = f"candidate follows: {raw}"
    result = parse_model_output(raw)
    assert result.status == "parse_failed"
    assert result.answer is None


@pytest.mark.parametrize("raw", ['[]', '"text"', '42', 'no json here'])
def test_parser_rejects_arrays_scalars_and_invalid_text(raw) -> None:
    result = parse_model_output(raw)
    assert result.status == "parse_failed"
    assert result.answer is None


def test_candidate_metadata_records_generation_and_status(tmp_path, rows) -> None:
    raw = json.dumps(rows[0]["messages"][2]["content"])
    config = _config(tmp_path, rows[:1], temperature=0.25, top_p=0.9, max_tokens=321)
    report, code = run_model_candidates(config, FakeClient([raw]))
    loaded, problems = load_jsonl(config.output)
    metadata = loaded[0][1]["metadata"]
    assert code == 0 and report["ok"] and not problems
    assert metadata["model"] == "fixture-model"
    assert metadata["prompt_template"] == "rtl_answer_v0.1_default"
    assert metadata["temperature"] == 0.25 and metadata["top_p"] == 0.9
    assert metadata["max_tokens"] == 321
    assert metadata["attempts"] == 1
    assert metadata["parse_status"] == "parsed_json"
    assert metadata["validation_status"] == "candidate_valid"


def test_dry_run_never_calls_client_and_writes_status(tmp_path, rows) -> None:
    client = FakeClient([])
    config = _config(tmp_path, rows, dry_run=True)
    report, code = run_model_candidates(config, client)
    loaded, _ = load_jsonl(config.output)
    assert code == 0 and report["ok"]
    assert client.calls == 0
    assert len(loaded) == 2
    assert report["parse_status_counts"] == {"dry_run": 2}
    assert all(row["metadata"]["attempts"] == 0 for _, row in loaded)


def test_nonlocal_endpoint_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow-nonlocal"):
        validate_endpoint("https://models.example/v1/chat/completions")
    assert validate_endpoint(
        "https://models.example/v1/chat/completions", allow_nonlocal=True
    ) == "models.example"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_local_endpoints_are_accepted(host) -> None:
    assert validate_endpoint(f"http://{host}:8000/v1/chat/completions/") in {
        "127.0.0.1", "localhost", "::1",
    }


@pytest.mark.parametrize("endpoint", [
    "http://user:secret@127.0.0.1:8000/v1/chat/completions",
    "http://127.0.0.1:8000/v1/chat/completions?debug=1",
    "http://127.0.0.1:8000/v1/chat/completions#fragment",
])
def test_endpoint_rejects_credentials_query_and_fragment(endpoint) -> None:
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


def test_api_key_value_is_not_serialized(tmp_path, rows, monkeypatch) -> None:
    secret = "unique-fixture-secret-value"
    monkeypatch.setenv("FIXTURE_MODEL_KEY", secret)
    raw = json.dumps(rows[0]["messages"][2]["content"])

    def fake_client(endpoint, api_key):
        assert api_key == secret
        return FakeClient([raw])

    monkeypatch.setattr(runner, "OpenAIChatClient", fake_client)
    config = _config(tmp_path, rows[:1], api_key_env="FIXTURE_MODEL_KEY")
    report, code = run_model_candidates(config)
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (config.output, *runner._report_paths(config.output))
    )
    assert code == 0 and report["ok"]
    assert secret not in serialized and secret not in json.dumps(report)


def test_existing_output_requires_resume_or_overwrite(tmp_path, rows) -> None:
    config = _config(tmp_path, rows, overwrite=False)
    config.output.parent.mkdir(parents=True)
    config.output.write_text("keep\n", encoding="utf-8")
    report, code = run_model_candidates(config, FakeClient([]))
    assert code == 1
    assert "output already exists" in " ".join(report["errors"])
    assert config.output.read_text(encoding="utf-8") == "keep\n"


def test_resume_skips_existing_ids(tmp_path, rows) -> None:
    config = _config(tmp_path, rows, overwrite=False, resume=True)
    existing = {
        "id": rows[0]["id"],
        "answer": deepcopy(rows[0]["messages"][2]["content"]),
        "metadata": {"model": "existing"},
    }
    write_jsonl(config.output, [existing])
    client = FakeClient([json.dumps(rows[1]["messages"][2]["content"])])
    report, code = run_model_candidates(config, client)
    loaded, _ = load_jsonl(config.output)
    assert code == 0 and client.calls == 1
    assert report["skipped_rows"] == 1 and report["written_rows"] == 1
    assert [row["id"] for _, row in loaded] == [rows[0]["id"], rows[1]["id"]]


def test_resume_rejects_duplicate_output_ids(tmp_path, rows) -> None:
    config = _config(tmp_path, rows, overwrite=False, resume=True)
    candidate = {"id": rows[0]["id"], "answer": rows[0]["messages"][2]["content"]}
    write_jsonl(config.output, [candidate, candidate])
    report, code = run_model_candidates(config, FakeClient([]))
    assert code == 1
    assert "duplicate candidate id" in " ".join(report["errors"])


def test_raw_output_uses_safe_filename(tmp_path, rows) -> None:
    path = safe_raw_output_path(tmp_path, "../../unsafe row/id")
    assert path.parent == tmp_path
    assert ".." not in path.name and "/" not in path.name
    config = _config(tmp_path, rows[:1], raw_output_dir=tmp_path / "raw")
    raw = json.dumps(rows[0]["messages"][2]["content"])
    report, code = run_model_candidates(config, FakeClient([raw]))
    loaded, _ = load_jsonl(config.output)
    raw_path = Path(loaded[0][1]["metadata"]["raw_output_path"])
    assert code == 0 and report["ok"]
    assert raw_path.parent == config.raw_output_dir and raw_path.read_text(encoding="utf-8") == raw


def test_overwrite_preserves_unknown_files(tmp_path, rows) -> None:
    config = _config(tmp_path, rows[:1])
    config.output.parent.mkdir(parents=True)
    unknown = config.output.parent / "reviewer_notes.md"
    unknown.write_text("keep\n", encoding="utf-8")
    raw = json.dumps(rows[0]["messages"][2]["content"])
    report, code = run_model_candidates(config, FakeClient([raw]))
    assert code == 0 and report["ok"]
    assert unknown.read_text(encoding="utf-8") == "keep\n"


def test_symlinked_managed_output_is_rejected(tmp_path, rows) -> None:
    config = _config(tmp_path, rows[:1])
    config.output.parent.mkdir(parents=True)
    target = tmp_path / "target.jsonl"
    target.write_text("keep\n", encoding="utf-8")
    try:
        config.output.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    report, code = run_model_candidates(config, FakeClient([]))
    assert code == 1 and "must not be a symlink" in " ".join(report["errors"])
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_symlinked_raw_directory_and_input_parent_are_rejected(tmp_path, rows) -> None:
    dataset = _dataset(tmp_path, rows[:1])
    raw_target = tmp_path / "raw-target"
    raw_target.mkdir()
    raw_link = tmp_path / "raw-link"
    try:
        raw_link.symlink_to(raw_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    config = RunnerConfig(
        dataset=dataset,
        output=dataset.parent / "candidates.jsonl",
        model="fixture-model",
        raw_output_dir=raw_link,
        overwrite=True,
    )
    report, code = run_model_candidates(config, FakeClient([]))
    errors = " ".join(report["errors"])
    assert code == 1
    assert "must not be a symlink" in errors
    assert "must not contain the dataset input" in errors


def test_symlinked_raw_managed_file_is_rejected(tmp_path, rows) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    config = _config(tmp_path, rows[:1], raw_output_dir=raw_dir)
    target = tmp_path / "raw-target.txt"
    target.write_text("keep\n", encoding="utf-8")
    raw_path = safe_raw_output_path(raw_dir, rows[0]["id"])
    try:
        raw_path.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    report, code = run_model_candidates(config, FakeClient([]))
    assert code == 1 and "must not be a symlink" in " ".join(report["errors"])
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_strict_mode_fails_on_parse_error_but_writes_candidate(tmp_path, rows) -> None:
    config = _config(tmp_path, rows[:1], strict=True)
    report, code = run_model_candidates(config, FakeClient(["not json"]))
    loaded, _ = load_jsonl(config.output)
    assert code == 1 and not report["ok"]
    assert report["parse_status_counts"] == {"parse_failed": 1}
    assert len(loaded) == 1 and isinstance(loaded[0][1]["answer"], dict)


def test_endpoint_failure_retries_and_returns_failure(tmp_path, rows) -> None:
    client = FailingClient()
    config = _config(tmp_path, rows[:1], retries=2)
    report, code = run_model_candidates(config, client)
    loaded, _ = load_jsonl(config.output)
    assert code == 1 and not report["ok"]
    assert client.calls == 3
    assert report["parse_status_counts"] == {"endpoint_failed": 1}
    assert loaded[0][1]["metadata"]["attempts"] == 3


def test_cli_dry_run_json_output_is_parseable(tmp_path) -> None:
    output = tmp_path / "cli.jsonl"
    completed = subprocess.run([
        sys.executable, "scripts/eval/run_model_candidates.py",
        "--dataset", str(GOLDEN), "--output", str(output),
        "--model", "dry-run-model", "--limit", "1", "--dry-run", "--json", "--overwrite",
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert payload["ok"] and payload["parse_status_counts"] == {"dry_run": 1}


def test_optional_evaluation_integration(tmp_path, rows) -> None:
    raw = json.dumps(rows[0]["messages"][2]["content"])
    config = _config(
        tmp_path,
        rows[:1],
        evaluate_output_dir=tmp_path / "evaluation",
    )
    report, code = run_model_candidates(config, FakeClient([raw]))
    assert code == 0 and report["evaluation"]["matched_rows"] == 1
    assert (config.evaluate_output_dir / "metrics.json").is_file()
