"""Orchestrate repeatable local model benchmarks and deterministic evaluation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any

from scripts.dataset.io_utils import write_jsonl
from scripts.eval.evaluator import evaluate_dataset, load_candidate_answers, load_dataset_rows
from scripts.eval.make_baseline_candidates import make_candidates
from scripts.eval.model_candidate_runner import (
    DEFAULT_ENDPOINT,
    RunnerConfig,
    run_model_candidates,
    validate_endpoint,
)
from scripts.eval.model_prompting import DEFAULT_PROMPT_TEMPLATE


REPO_ROOT = Path(__file__).resolve().parents[2]
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
SUMMARY_FILES = (
    "benchmark_config.resolved.json",
    "benchmark_summary.json",
    "benchmark_summary.md",
    "benchmark_summary.csv",
)
EVALUATION_FILES = ("row_results.jsonl", "unmatched_candidates.jsonl", "metrics.json", "report.md")
TOP_LEVEL_CONFIG_KEYS = {"run_id", "dataset", "include_rule_baseline", "candidate_dir", "eval_dir", "defaults", "models"}
DEFAULT_CONFIG_KEYS = {
    "endpoint", "api_key_env", "prompt_template", "temperature", "top_p", "max_tokens",
    "timeout", "retries", "strict",
}
MODEL_CONFIG_KEYS = DEFAULT_CONFIG_KEYS | {"name", "model", "raw_output_dir", "allow_nonlocal_endpoint"}


@dataclass(frozen=True)
class SuiteOptions:
    config: Path
    output_dir: Path
    run_id: str | None = None
    limit: int | None = None
    row_ids: tuple[str, ...] = ()
    dry_run: bool = False
    resume: bool = False
    overwrite: bool = False
    skip_candidates: bool = False
    evaluate_only: bool = False
    allow_nonlocal_endpoint: bool = False


def is_safe_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(SAFE_NAME.fullmatch(name))
        and name not in {".", ".."}
        and name.upper() not in WINDOWS_RESERVED
        and not name.endswith((".", " "))
    )


def _inside_local_data(path: Path) -> bool:
    return any(
        part.lower() == ".local_data"
        for candidate in (path.absolute(), path.resolve())
        for part in candidate.parts
    )


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _symlink_in_ancestry(path: Path) -> Path | None:
    current = path.absolute()
    while True:
        if current.is_symlink():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _dangerous_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved.parent == resolved or resolved in {REPO_ROOT, Path.home().resolve()}


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_benchmark_config(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"benchmark config not found: {path}"]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"could not read benchmark config: {exc}"]
    if not isinstance(value, dict):
        return None, ["benchmark config must be a JSON object"]
    return value, []


def _effective_models(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        return [], ["defaults must be an object"]
    unsupported_defaults = sorted(set(defaults) - DEFAULT_CONFIG_KEYS)
    if unsupported_defaults:
        errors.append(f"unsupported defaults fields: {', '.join(unsupported_defaults)}")
    models = config.get("models")
    if not isinstance(models, list):
        return [], ["models must be an array"]
    effective: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            errors.append(f"models[{index}] must be an object")
            continue
        unsupported_model = sorted(set(model) - MODEL_CONFIG_KEYS)
        if unsupported_model:
            errors.append(f"unsupported fields in models[{index}]: {', '.join(unsupported_model)}")
        merged = dict(defaults)
        merged.update(model)
        name = merged.get("name")
        if not is_safe_name(name):
            errors.append(f"models[{index}].name must be a filesystem-safe name")
            continue
        if name in names:
            errors.append(f"duplicate model name: {name}")
            continue
        names.add(name)
        if not isinstance(merged.get("model"), str) or not merged["model"].strip():
            errors.append(f"model identifier must be non-empty for {name}")
            continue
        merged.setdefault("endpoint", DEFAULT_ENDPOINT)
        merged.setdefault("prompt_template", DEFAULT_PROMPT_TEMPLATE)
        merged.setdefault("temperature", 0.0)
        merged.setdefault("top_p", None)
        merged.setdefault("max_tokens", 8192)
        merged.setdefault("timeout", 120.0)
        merged.setdefault("retries", 1)
        merged.setdefault("strict", False)
        if not isinstance(merged["endpoint"], str):
            errors.append(f"endpoint must be a string for {name}")
        if not isinstance(merged["prompt_template"], str):
            errors.append(f"prompt_template must be a string for {name}")
        if not isinstance(merged["temperature"], (int, float)) or isinstance(merged["temperature"], bool):
            errors.append(f"temperature must be numeric for {name}")
        if merged["top_p"] is not None and (not isinstance(merged["top_p"], (int, float)) or isinstance(merged["top_p"], bool)):
            errors.append(f"top_p must be numeric or null for {name}")
        for field in ("max_tokens", "retries"):
            if not isinstance(merged[field], int) or isinstance(merged[field], bool):
                errors.append(f"{field} must be an integer for {name}")
        if not isinstance(merged["timeout"], (int, float)) or isinstance(merged["timeout"], bool):
            errors.append(f"timeout must be numeric for {name}")
        if not isinstance(merged["strict"], bool):
            errors.append(f"strict must be boolean for {name}")
        if merged.get("api_key_env") is not None and not isinstance(merged["api_key_env"], str):
            errors.append(f"api_key_env must be a string for {name}")
        if merged.get("allow_nonlocal_endpoint") is not None and not isinstance(merged["allow_nonlocal_endpoint"], bool):
            errors.append(f"allow_nonlocal_endpoint must be boolean for {name}")
        effective.append(merged)
    if config.get("include_rule_baseline") and "rule_baseline" in names:
        errors.append("model name rule_baseline is reserved when include_rule_baseline is true")
    return effective, errors


def _empty_summary(options: SuiteOptions) -> dict[str, Any]:
    return {
        "ok": False,
        "run_id": options.run_id,
        "dataset": "",
        "output_dir": str(options.output_dir),
        "limit": options.limit,
        "row_ids": list(options.row_ids),
        "dry_run": options.dry_run,
        "results": [],
        "errors": [],
        "warnings": [],
    }


def _validate_directory(path: Path, label: str, dataset: Path, errors: list[str]) -> None:
    if _inside_local_data(path):
        errors.append(f"{label} must not be inside .local_data: {path}")
    if path.is_symlink():
        errors.append(f"{label} must not be a symlink: {path}")
        return
    ancestor = _symlink_in_ancestry(path.parent)
    if ancestor is not None:
        errors.append(f"{label} ancestry must not contain a symlink: {ancestor}")
        return
    if path.exists() and not path.is_dir():
        errors.append(f"{label} must be a directory path: {path}")
    if _dangerous_root(path):
        errors.append(f"{label} must not be a filesystem, repository, or home root: {path}")
    if _is_within(dataset, path):
        errors.append(f"{label} must not contain the dataset input: {path}")


def _validate_managed_file(path: Path, label: str, errors: list[str]) -> None:
    if path.is_symlink():
        errors.append(f"{label} must not be a symlink: {path}")
    elif path.exists() and not path.is_file():
        errors.append(f"{label} must be a file path: {path}")


def _paths_overlap(left: Path, right: Path) -> bool:
    return _is_within(left, right) or _is_within(right, left)


def _candidate_path(candidate_dir: Path, run_id: str, name: str) -> Path:
    return candidate_dir / f"{run_id}__{name}.jsonl"


def _eval_path(eval_dir: Path, run_id: str, name: str) -> Path:
    return eval_dir / f"{run_id}__{name}"


def _select_rows(rows: list[dict[str, Any]], row_ids: tuple[str, ...], limit: int | None) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    selected = rows
    if row_ids:
        requested = set(row_ids)
        available = {str(row["id"]) for row in rows}
        missing = sorted(requested - available)
        if missing:
            errors.append(f"requested row IDs not found: {', '.join(missing)}")
        selected = [row for row in rows if row["id"] in requested]
    if limit is not None:
        selected = selected[:limit]
    return selected, errors


def _evaluation_result(dataset_rows: list[dict[str, Any]], dataset: Path, candidate: Path, output: Path, strict: bool) -> tuple[dict[str, Any], int]:
    loaded = load_candidate_answers(candidate)
    if loaded.errors:
        return {
            "ok": False,
            "dataset_rows": len(dataset_rows),
            "candidate_rows": loaded.rows,
            "matched_rows": 0,
            "mean_score": 0.0,
            "safety_failures": 0,
            "output_dir": str(output),
            "errors": loaded.errors,
            "warnings": loaded.warnings,
        }, 1
    return evaluate_dataset(dataset_rows, loaded.candidates, loaded.rows, output, dataset, candidate, strict=strict)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _aggregate(
    name: str,
    kind: str,
    model: dict[str, Any] | None,
    candidate: Path,
    evaluation_dir: Path,
    candidate_report: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
) -> dict[str, Any]:
    candidate_report = candidate_report or {}
    evaluation = evaluation or {}
    metrics = _read_json(evaluation_dir / "metrics.json") or {}
    parse_counts = candidate_report.get("parse_status_counts", {})
    validation_counts = candidate_report.get("validation_status_counts", {})
    model = model or {}
    endpoint_host = candidate_report.get("endpoint_host", "local_rule" if kind == "rule_baseline" else "unknown")
    return {
        "name": name,
        "kind": kind,
        "candidate_ok": bool(candidate_report.get("ok", candidate.exists())),
        "evaluation_ok": bool(evaluation.get("ok", metrics.get("matched_rows", 0) > 0)),
        "candidate_rows": int(evaluation.get("candidate_rows", candidate_report.get("written_rows", 0))),
        "matched_rows": int(evaluation.get("matched_rows", metrics.get("matched_rows", 0))),
        "mean_score": float(evaluation.get("mean_score", metrics.get("mean_score", 0.0))),
        "median_score": float(metrics.get("median_score", 0.0)),
        "safety_failures": int(evaluation.get("safety_failures", metrics.get("safety_failures", 0))),
        "parse_failed": int(parse_counts.get("parse_failed", 0)),
        "endpoint_failed": int(parse_counts.get("endpoint_failed", 0)),
        "candidate_invalid": int(validation_counts.get("candidate_invalid", 0)),
        "candidate_output": str(candidate),
        "evaluation_output_dir": str(evaluation_dir),
        "model": model.get("model", "rule_baseline"),
        "endpoint_host": endpoint_host,
        "prompt_template": model.get("prompt_template", "baseline_v0.1"),
        "temperature": model.get("temperature"),
        "max_tokens": model.get("max_tokens"),
        "errors": list(candidate_report.get("errors", [])) + list(evaluation.get("errors", [])),
    }


def _write_model_artifacts(output_dir: Path, result: dict[str, Any], candidate_report: dict[str, Any] | None, evaluation: dict[str, Any] | None) -> None:
    model_dir = output_dir / "models" / result["name"]
    model_dir.mkdir(parents=True, exist_ok=True)
    _atomic_text(model_dir / "candidate_report.json", json.dumps(candidate_report or {}, indent=2) + "\n")
    metrics = _read_json(Path(result["evaluation_output_dir"]) / "metrics.json") or evaluation or {}
    _atomic_text(model_dir / "evaluation_metrics.json", json.dumps(metrics, indent=2) + "\n")
    links = {
        "candidate_output": result["candidate_output"],
        "evaluation_output_dir": result["evaluation_output_dir"],
    }
    _atomic_text(model_dir / "links.json", json.dumps(links, indent=2) + "\n")


def _sort_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(results, key=lambda item: (-item["mean_score"], item["safety_failures"], item["name"]))


def _summary_markdown(summary: dict[str, Any], rerun: str) -> str:
    rows = "\n".join(
        f"| `{item['name']}` | {item['kind']} | {item['mean_score']:.6f} | {item['matched_rows']} | "
        f"{item['parse_failed']} | {item['candidate_invalid']} | {item['safety_failures']} |"
        for item in _sort_results(summary["results"])
    ) or "| none | | | | | | |"
    failures = "\n".join(
        f"- `{item['name']}`: {'; '.join(item['errors'])}"
        for item in summary["results"] if item["errors"]
    ) or "- none"
    return f"""# Model benchmark suite

## Run settings

- Run ID: `{summary['run_id']}`
- Dataset: `{summary['dataset']}`
- Limit: {summary['limit']}
- Row IDs: `{json.dumps(summary['row_ids'])}`
- Dry run: {summary['dry_run']}

## Results

| Name | Kind | Mean score | Matched | Parse failed | Invalid | Safety failures |
|---|---|---:|---:|---:|---:|---:|
{rows}

## Failures

{failures}

## Rerun

```bash
{rerun}
```

## Limitations

Scores are deterministic structural and evidence-safety heuristics, not proof of RTL correctness. The suite does not execute RTL, testbenches, or EDA tools and does not establish statistical significance.
"""


def _summary_csv(results: list[dict[str, Any]]) -> str:
    fields = [
        "name", "kind", "candidate_ok", "evaluation_ok", "candidate_rows", "matched_rows",
        "mean_score", "median_score", "safety_failures", "parse_failed", "endpoint_failed",
        "candidate_invalid", "candidate_output", "evaluation_output_dir", "model", "endpoint_host",
        "prompt_template", "temperature", "max_tokens",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(results)
    return stream.getvalue()


def _rerun_command(options: SuiteOptions) -> str:
    args = [
        "python", "scripts/eval/run_benchmark_suite.py", "--config", str(options.config),
        "--output-dir", str(options.output_dir),
    ]
    if options.run_id:
        args.extend(["--run-id", options.run_id])
    if options.limit is not None:
        args.extend(["--limit", str(options.limit)])
    for row_id in options.row_ids:
        args.extend(["--row-id", row_id])
    for enabled, flag in (
        (options.dry_run, "--dry-run"), (options.resume, "--resume"),
        (options.overwrite, "--overwrite"), (options.skip_candidates, "--skip-candidates"),
        (options.evaluate_only, "--evaluate-only"),
        (options.allow_nonlocal_endpoint, "--allow-nonlocal-endpoint"),
    ):
        if enabled:
            args.append(flag)
    return shlex.join(args)


def run_benchmark_suite(options: SuiteOptions) -> tuple[dict[str, Any], int]:
    summary = _empty_summary(options)
    config, config_errors = load_benchmark_config(options.config)
    if config is None:
        summary["errors"].extend(config_errors)
        return summary, 1
    errors = summary["errors"]
    errors.extend(config_errors)
    unsupported_top_level = sorted(set(config) - TOP_LEVEL_CONFIG_KEYS)
    if unsupported_top_level:
        errors.append(f"unsupported benchmark config fields: {', '.join(unsupported_top_level)}")
    if options.resume and options.overwrite:
        errors.append("--resume and --overwrite are mutually exclusive")
    if options.skip_candidates and options.evaluate_only:
        errors.append("--skip-candidates and --evaluate-only are mutually exclusive")
    if options.limit is not None and options.limit <= 0:
        errors.append("limit must be greater than zero")
    configured_run_id = config.get("run_id")
    if not is_safe_name(configured_run_id):
        errors.append("config run_id must be a filesystem-safe name")
    run_id = options.run_id or configured_run_id
    if not is_safe_name(run_id):
        errors.append("run_id must be a filesystem-safe name")
    summary["run_id"] = run_id
    dataset_value = config.get("dataset")
    if not isinstance(dataset_value, str) or not dataset_value:
        errors.append("dataset must be a non-empty path string")
        dataset = Path("missing-dataset")
    else:
        dataset = Path(dataset_value)
    summary["dataset"] = str(dataset)
    models, model_errors = _effective_models(config)
    errors.extend(model_errors)
    include_baseline = config.get("include_rule_baseline", False)
    if not isinstance(include_baseline, bool):
        errors.append("include_rule_baseline must be boolean")
    if "candidate_dir" in config and not isinstance(config["candidate_dir"], str):
        errors.append("candidate_dir must be a path string")
    if "eval_dir" in config and not isinstance(config["eval_dir"], str):
        errors.append("eval_dir must be a path string")
    candidate_dir = Path(config["candidate_dir"]) if isinstance(config.get("candidate_dir"), str) else options.output_dir.parent / f"{options.output_dir.name}_candidates"
    eval_dir = Path(config["eval_dir"]) if isinstance(config.get("eval_dir"), str) else options.output_dir.parent / f"{options.output_dir.name}_evaluations"
    for path, label in ((options.output_dir, "output directory"), (candidate_dir, "candidate directory"), (eval_dir, "evaluation directory")):
        _validate_directory(path, label, dataset, errors)
    roots = [(options.output_dir, "output"), (candidate_dir, "candidate"), (eval_dir, "evaluation")]
    for index, (left, left_name) in enumerate(roots):
        for right, right_name in roots[index + 1:]:
            if _paths_overlap(left, right):
                errors.append(f"{left_name} and {right_name} directories must not overlap")
    for filename in SUMMARY_FILES:
        _validate_managed_file(options.output_dir / filename, f"suite output {filename}", errors)
    if (
        options.output_dir.exists() and not options.output_dir.is_symlink()
        and options.output_dir.is_dir() and any(options.output_dir.iterdir())
        and not options.resume and not options.overwrite
    ):
        errors.append(f"output directory is non-empty; use --resume or --overwrite: {options.output_dir}")
    dataset_rows, dataset_errors = load_dataset_rows(dataset)
    errors.extend(dataset_errors)
    selected_rows, selection_errors = _select_rows(dataset_rows, options.row_ids, options.limit)
    errors.extend(selection_errors)
    if not selected_rows and not dataset_errors:
        errors.append("row selection is empty")

    jobs: list[tuple[str, str, dict[str, Any] | None]] = []
    if include_baseline:
        jobs.append(("rule_baseline", "rule_baseline", None))
    jobs.extend((model["name"], "model", model) for model in models)
    raw_directories: list[tuple[Path, str]] = []
    for name, _, model in jobs:
        candidate = _candidate_path(candidate_dir, str(run_id), name)
        evaluation = _eval_path(eval_dir, str(run_id), name)
        model_dir = options.output_dir / "models" / name
        for path, label in ((evaluation, f"evaluation output for {name}"), (model_dir, f"suite model output for {name}")):
            _validate_directory(path, label, dataset, errors)
        _validate_managed_file(candidate, f"candidate output for {name}", errors)
        _validate_managed_file(
            candidate.with_name(f"{candidate.stem}.report.json"),
            f"candidate report for {name}",
            errors,
        )
        for filename in EVALUATION_FILES:
            _validate_managed_file(evaluation / filename, f"evaluation output {filename} for {name}", errors)
        for filename in ("candidate_report.json", "evaluation_metrics.json", "links.json"):
            _validate_managed_file(model_dir / filename, f"suite model output {filename} for {name}", errors)
        if model is not None:
            raw_dir_value = model.get("raw_output_dir")
            if raw_dir_value is not None and not isinstance(raw_dir_value, str):
                errors.append(f"raw_output_dir must be a path string for {name}")
            elif raw_dir_value:
                raw_directory = Path(raw_dir_value)
                _validate_directory(raw_directory, f"raw output directory for {name}", dataset, errors)
                for root, root_name in roots:
                    if _paths_overlap(raw_directory, root):
                        errors.append(f"raw output directory for {name} must not overlap {root_name} directory")
                for existing_raw, existing_name in raw_directories:
                    if _paths_overlap(raw_directory, existing_raw):
                        errors.append(f"raw output directories for {name} and {existing_name} must not overlap")
                raw_directories.append((raw_directory, name))
            allowed = bool(model.get("allow_nonlocal_endpoint")) and options.allow_nonlocal_endpoint
            try:
                validate_endpoint(str(model["endpoint"]), allow_nonlocal=allowed)
            except ValueError as exc:
                errors.append(f"invalid endpoint for {name}: {exc}")
        if not options.skip_candidates and not options.evaluate_only:
            exists = candidate.exists() or candidate.is_symlink()
            if exists and not options.resume and not options.overwrite:
                errors.append(f"candidate output exists; use --resume or --overwrite: {candidate}")
        if options.evaluate_only and (candidate.is_symlink() or not candidate.is_file()):
            errors.append(f"candidate output required for --evaluate-only: {candidate}")
    if errors:
        return summary, 1

    resolved = {
        "run_id": run_id,
        "dataset": str(dataset),
        "include_rule_baseline": include_baseline,
        "candidate_dir": str(candidate_dir),
        "eval_dir": str(eval_dir),
        "defaults": config.get("defaults", {}),
        "models": models,
        "limit": options.limit,
        "row_ids": list(options.row_ids),
        "dry_run": options.dry_run,
    }
    options.output_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)
    _atomic_text(options.output_dir / "benchmark_config.resolved.json", json.dumps(resolved, indent=2) + "\n")

    for name, kind, model in jobs:
        candidate = _candidate_path(candidate_dir, str(run_id), name)
        evaluation_dir = _eval_path(eval_dir, str(run_id), name)
        candidate_report: dict[str, Any] | None = None
        evaluation: dict[str, Any] | None = None
        evaluation_code = 0
        if options.skip_candidates:
            candidate_report = (
                _read_json(candidate.with_name(f"{candidate.stem}.report.json"))
                or _read_json(options.output_dir / "models" / name / "candidate_report.json")
            )
            evaluation = _read_json(evaluation_dir / "metrics.json")
            if candidate_report is None and not candidate.exists():
                summary["errors"].append(f"missing candidate artifacts for {name}")
            if evaluation is None:
                summary["errors"].append(f"missing evaluation metrics for {name}")
        else:
            if not options.evaluate_only:
                if kind == "rule_baseline":
                    if options.resume and candidate.exists():
                        candidate_report = {"ok": True, "written_rows": len(load_candidate_answers(candidate).candidates), "errors": [], "warnings": []}
                    else:
                        if len(selected_rows) == len(dataset_rows):
                            baseline_dataset = dataset
                            candidate_report, _ = make_candidates(baseline_dataset, candidate)
                        else:
                            with tempfile.TemporaryDirectory() as temporary:
                                baseline_dataset = Path(temporary) / "selected.jsonl"
                                write_jsonl(baseline_dataset, selected_rows)
                                candidate_report, _ = make_candidates(baseline_dataset, candidate)
                else:
                    assert model is not None
                    candidate_report, _ = run_model_candidates(RunnerConfig(
                        dataset=dataset,
                        output=candidate,
                        model=model["model"],
                        endpoint=model["endpoint"],
                        api_key_env=model.get("api_key_env"),
                        prompt_template=model["prompt_template"],
                        temperature=float(model["temperature"]),
                        top_p=float(model["top_p"]) if model.get("top_p") is not None else None,
                        max_tokens=int(model["max_tokens"]),
                        timeout=float(model["timeout"]),
                        retries=int(model["retries"]),
                        limit=options.limit,
                        row_ids=options.row_ids,
                        resume=options.resume and candidate.exists(),
                        overwrite=options.overwrite,
                        raw_output_dir=Path(model["raw_output_dir"]) if model.get("raw_output_dir") else None,
                        strict=bool(model["strict"]),
                        dry_run=options.dry_run,
                        allow_nonlocal_endpoint=bool(model.get("allow_nonlocal_endpoint")) and options.allow_nonlocal_endpoint,
                    ))
            else:
                candidate_report = _read_json(candidate.with_name(f"{candidate.stem}.report.json")) or {"ok": True, "errors": [], "warnings": []}
            if candidate_report and candidate_report.get("ok"):
                evaluation, evaluation_code = _evaluation_result(
                    selected_rows,
                    dataset,
                    candidate,
                    evaluation_dir,
                    strict=bool(model.get("strict")) if model else False,
                )
            else:
                summary["errors"].append(f"candidate generation failed for {name}")
        result = _aggregate(name, kind, model, candidate, evaluation_dir, candidate_report, evaluation)
        summary["results"].append(result)
        _write_model_artifacts(options.output_dir, result, candidate_report, evaluation)
        if evaluation_code and not options.dry_run:
            summary["errors"].append(f"evaluation failed for {name}")
        elif evaluation_code and options.dry_run:
            summary["warnings"].append(f"dry-run placeholders failed evaluator validation for {name}")

    summary["ok"] = not summary["errors"]
    rerun = _rerun_command(options)
    _atomic_text(options.output_dir / "benchmark_summary.json", json.dumps(summary, indent=2) + "\n")
    _atomic_text(options.output_dir / "benchmark_summary.md", _summary_markdown(summary, rerun))
    _atomic_text(options.output_dir / "benchmark_summary.csv", _summary_csv(summary["results"]))
    return summary, 0 if summary["ok"] else 1
