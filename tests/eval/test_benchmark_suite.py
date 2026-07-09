from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.eval.benchmark_suite as benchmark
from scripts.dataset.io_utils import write_jsonl
from scripts.eval.benchmark_suite import SuiteOptions, _sort_results, run_benchmark_suite
from tests.dataset.conftest import GOLDEN, ROOT


def _valid_answer(row: dict) -> dict:
    answer = json.loads(json.dumps(row["messages"][2]["content"]))
    answer.setdefault("source_id", row.get("source_id", row["id"]))
    answer.setdefault("evidence_used", ["tool_checks"])
    answer.setdefault("limitations", ["No external tool evidence was added by this benchmark test helper."])
    return answer


def _config(tmp_path: Path, *, models: list[dict] | None = None, baseline: bool = False, **extra) -> Path:
    value = {
        "run_id": "fixture_suite",
        "dataset": str(GOLDEN),
        "include_rule_baseline": baseline,
        "defaults": {
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "prompt_template": "rtl_answer_v0.1_default",
            "temperature": 0.0,
            "max_tokens": 8192,
            "timeout": 120,
            "retries": 1,
            "strict": False,
        },
        "models": models or [],
    }
    value.update(extra)
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _model(name: str, **extra) -> dict:
    value = {"name": name, "model": f"{name}-model"}
    value.update(extra)
    return value


def _options(tmp_path: Path, config: Path, **changes) -> SuiteOptions:
    values = {"config": config, "output_dir": tmp_path / "suite", "overwrite": True}
    values.update(changes)
    return SuiteOptions(**values)


def test_config_rejects_duplicate_model_names(tmp_path) -> None:
    config = _config(tmp_path, models=[_model("same"), _model("same")])
    summary, code = run_benchmark_suite(_options(tmp_path, config, dry_run=True))
    assert code == 1
    assert "duplicate model name" in " ".join(summary["errors"])


@pytest.mark.parametrize("name", ["../escape", "has space", "CON", ".hidden", "name/child"])
def test_config_rejects_unsafe_model_names(tmp_path, name) -> None:
    config = _config(tmp_path, models=[_model(name)])
    summary, code = run_benchmark_suite(_options(tmp_path, config, dry_run=True))
    assert code == 1
    assert "filesystem-safe" in " ".join(summary["errors"])


def test_nonlocal_endpoint_requires_config_and_cli_opt_in(tmp_path) -> None:
    endpoint = "https://models.example/v1/chat/completions"
    denied = _config(tmp_path, models=[_model("remote", endpoint=endpoint, allow_nonlocal_endpoint=True)])
    summary, code = run_benchmark_suite(_options(tmp_path, denied, dry_run=True))
    assert code == 1 and "allow-nonlocal" in " ".join(summary["errors"])

    allowed = _config(tmp_path, models=[_model("remote", endpoint=endpoint, allow_nonlocal_endpoint=True)])
    summary, code = run_benchmark_suite(_options(
        tmp_path, allowed, dry_run=True, allow_nonlocal_endpoint=True,
    ))
    assert code == 0 and summary["ok"]


def test_secret_bearing_unknown_config_field_is_rejected(tmp_path) -> None:
    config = _config(tmp_path, models=[_model("unsafe", api_key="do-not-store")])
    summary, code = run_benchmark_suite(_options(tmp_path, config, dry_run=True))
    assert code == 1
    assert "unsupported fields" in " ".join(summary["errors"])
    assert not (_options(tmp_path, config).output_dir / "benchmark_config.resolved.json").exists()


@pytest.mark.parametrize("location", ["top", "defaults", "model"])
def test_unsupported_config_fields_are_rejected(tmp_path, location) -> None:
    model = _model("fixture")
    extra: dict = {}
    if location == "top":
        extra["unexpected"] = True
    elif location == "defaults":
        extra["defaults"] = {"unexpected": True}
    else:
        model["unexpected"] = True
    config = _config(tmp_path, models=[model], **extra)
    summary, code = run_benchmark_suite(_options(tmp_path, config, dry_run=True))
    assert code == 1 and "unsupported" in " ".join(summary["errors"])


def test_resolved_config_contains_env_name_not_secret_value(tmp_path, monkeypatch) -> None:
    secret = "unique-benchmark-secret-value"
    monkeypatch.setenv("FIXTURE_BENCHMARK_KEY", secret)
    config = _config(tmp_path, models=[_model("local", api_key_env="FIXTURE_BENCHMARK_KEY")])
    options = _options(tmp_path, config, dry_run=True)
    summary, code = run_benchmark_suite(options)
    resolved = (options.output_dir / "benchmark_config.resolved.json").read_text(encoding="utf-8")
    assert code == 0 and summary["ok"]
    assert "FIXTURE_BENCHMARK_KEY" in resolved and secret not in resolved


def test_dry_run_creates_summaries_without_endpoint_calls(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, models=[_model("local")])
    calls = 0
    original = benchmark.run_model_candidates

    def guarded_runner(runner_config):
        nonlocal calls
        assert runner_config.dry_run is True
        calls += 1
        return original(runner_config)

    monkeypatch.setattr(benchmark, "run_model_candidates", guarded_runner)
    options = _options(tmp_path, config, dry_run=True)
    summary, code = run_benchmark_suite(options)
    assert code == 0 and summary["ok"] and calls == 1
    for name in benchmark.SUMMARY_FILES:
        assert (options.output_dir / name).is_file()


def test_rule_baseline_is_generated_and_evaluated(tmp_path) -> None:
    config = _config(tmp_path, baseline=True)
    summary, code = run_benchmark_suite(_options(tmp_path, config, limit=2))
    result = summary["results"][0]
    assert code == 0 and result["name"] == "rule_baseline"
    assert result["candidate_rows"] == result["matched_rows"] == 2
    assert result["evaluation_ok"] is True


def test_suite_calls_runner_for_each_model(tmp_path, monkeypatch) -> None:
    models = [_model("alpha"), _model("beta")]
    config = _config(tmp_path, models=models)
    rows = [json.loads(GOLDEN.read_text(encoding="utf-8").splitlines()[0])]
    called: list[str] = []

    def fake_runner(runner_config):
        called.append(runner_config.model)
        write_jsonl(runner_config.output, [{
            "id": rows[0]["id"],
            "answer": _valid_answer(rows[0]),
            "metadata": {"model": runner_config.model},
        }])
        return {
            "ok": True, "written_rows": 1, "endpoint_host": "127.0.0.1",
            "parse_status_counts": {"parsed_json": 1},
            "validation_status_counts": {"candidate_valid": 1},
            "errors": [], "warnings": [],
        }, 0

    monkeypatch.setattr(benchmark, "run_model_candidates", fake_runner)
    summary, code = run_benchmark_suite(_options(tmp_path, config, limit=1))
    assert code == 0 and called == ["alpha-model", "beta-model"]
    assert len(summary["results"]) == 2


def test_aggregation_sort_order() -> None:
    results = [
        {"name": "z", "mean_score": 0.8, "safety_failures": 0},
        {"name": "b", "mean_score": 0.9, "safety_failures": 2},
        {"name": "a", "mean_score": 0.9, "safety_failures": 1},
    ]
    assert [item["name"] for item in _sort_results(results)] == ["a", "b", "z"]


def test_csv_summary_is_parseable(tmp_path) -> None:
    config = _config(tmp_path, baseline=True)
    options = _options(tmp_path, config, limit=1)
    summary, code = run_benchmark_suite(options)
    rows = list(csv.DictReader(io.StringIO(
        (options.output_dir / "benchmark_summary.csv").read_text(encoding="utf-8")
    )))
    assert code == 0 and len(rows) == len(summary["results"]) == 1
    assert rows[0]["name"] == "rule_baseline"
    payload = json.loads((options.output_dir / "benchmark_summary.json").read_text(encoding="utf-8"))
    markdown = (options.output_dir / "benchmark_summary.md").read_text(encoding="utf-8")
    assert payload["ok"] is True and "| `rule_baseline` |" in markdown


@pytest.mark.parametrize("changes, message", [
    ({"resume": True, "overwrite": True}, "resume and --overwrite"),
    ({"skip_candidates": True, "evaluate_only": True}, "skip-candidates and --evaluate-only"),
])
def test_mutually_exclusive_modes_are_rejected(tmp_path, changes, message) -> None:
    config = _config(tmp_path, baseline=True)
    summary, code = run_benchmark_suite(_options(tmp_path, config, **changes))
    assert code == 1 and message in " ".join(summary["errors"])


def test_nonempty_output_requires_resume_or_overwrite(tmp_path) -> None:
    config = _config(tmp_path, baseline=True)
    output = tmp_path / "suite"
    output.mkdir()
    keep = output / "keep.txt"
    keep.write_text("keep\n", encoding="utf-8")
    summary, code = run_benchmark_suite(SuiteOptions(config=config, output_dir=output))
    assert code == 1 and "non-empty" in " ".join(summary["errors"])
    assert keep.read_text(encoding="utf-8") == "keep\n"


def test_overwrite_preserves_unknown_files(tmp_path) -> None:
    config = _config(tmp_path, baseline=True)
    options = _options(tmp_path, config, limit=1)
    options.output_dir.mkdir()
    unknown = options.output_dir / "reviewer_notes.md"
    parent_keep = options.output_dir.parent / "keep.txt"
    unknown.write_text("keep\n", encoding="utf-8")
    parent_keep.write_text("keep\n", encoding="utf-8")
    summary, code = run_benchmark_suite(options)
    assert code == 0 and summary["ok"]
    assert unknown.read_text(encoding="utf-8") == "keep\n"
    assert parent_keep.read_text(encoding="utf-8") == "keep\n"


def test_symlinked_output_directory_is_rejected_without_touching_target(tmp_path) -> None:
    config = _config(tmp_path, baseline=True)
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "suite"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    summary, code = run_benchmark_suite(SuiteOptions(config=config, output_dir=link, overwrite=True))
    assert code == 1 and "must not be a symlink" in " ".join(summary["errors"])
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_symlinked_output_ancestry_is_rejected(tmp_path) -> None:
    config = _config(tmp_path, baseline=True)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-parent"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    summary, code = run_benchmark_suite(SuiteOptions(
        config=config, output_dir=link / "suite", overwrite=True,
    ))
    assert code == 1 and "ancestry must not contain a symlink" in " ".join(summary["errors"])


@pytest.mark.parametrize("dangerous", [ROOT, Path.home(), Path(Path.home().anchor)])
def test_dangerous_output_roots_are_rejected(tmp_path, dangerous) -> None:
    config = _config(tmp_path, baseline=True)
    summary, code = run_benchmark_suite(SuiteOptions(
        config=config, output_dir=dangerous, overwrite=True,
    ))
    assert code == 1 and "must not be a filesystem, repository, or home root" in " ".join(summary["errors"])


def test_output_root_must_not_contain_dataset(tmp_path) -> None:
    dataset_dir = tmp_path / "input"
    dataset_dir.mkdir()
    dataset = dataset_dir / "dataset.jsonl"
    dataset.write_text(GOLDEN.read_text(encoding="utf-8"), encoding="utf-8")
    config = _config(tmp_path, baseline=True, dataset=str(dataset))
    summary, code = run_benchmark_suite(SuiteOptions(
        config=config, output_dir=dataset_dir, overwrite=True,
    ))
    assert code == 1 and "must not contain the dataset input" in " ".join(summary["errors"])


def test_evaluate_only_uses_existing_candidates(tmp_path) -> None:
    model = _model("existing")
    config = _config(tmp_path, models=[model])
    options = _options(tmp_path, config, evaluate_only=True, limit=1)
    candidate_dir = options.output_dir.parent / f"{options.output_dir.name}_candidates"
    candidate_dir.mkdir()
    row = json.loads(GOLDEN.read_text(encoding="utf-8").splitlines()[0])
    write_jsonl(candidate_dir / "fixture_suite__existing.jsonl", [{
        "id": row["id"], "answer": _valid_answer(row), "metadata": {"model": "existing"},
    }])
    summary, code = run_benchmark_suite(options)
    assert code == 0 and summary["results"][0]["matched_rows"] == 1


def test_cli_json_output_is_parseable(tmp_path) -> None:
    config = _config(tmp_path, models=[_model("cli")])
    output = tmp_path / "cli-suite"
    completed = subprocess.run([
        sys.executable, "scripts/eval/run_benchmark_suite.py",
        "--config", str(config), "--output-dir", str(output),
        "--dry-run", "--json", "--overwrite", "--limit", "1",
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert payload["ok"] and len(payload["results"]) == 1
