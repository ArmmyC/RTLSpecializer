"""Safe local/OpenAI-compatible model candidate generation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Protocol
from urllib import error, parse, request

from scripts.dataset.io_utils import load_jsonl
from scripts.eval.evaluator import (
    _candidate_validation_errors,
    evaluate_dataset,
    load_candidate_answers,
    load_dataset_rows,
)
from scripts.eval.model_prompting import (
    DEFAULT_PROMPT_TEMPLATE,
    PROMPT_VERSION,
    SUPPORTED_PROMPT_TEMPLATES,
    build_prompt,
)


DEFAULT_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_FILES = ("row_results.jsonl", "unmatched_candidates.jsonl", "metrics.json", "report.md")


@dataclass(frozen=True)
class ParseResult:
    answer: dict[str, Any] | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class RunnerConfig:
    dataset: Path
    output: Path
    model: str
    endpoint: str = DEFAULT_ENDPOINT
    api_key_env: str | None = None
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    temperature: float = 0.0
    top_p: float | None = None
    max_tokens: int = 2048
    timeout: float = 120.0
    retries: int = 1
    limit: int | None = None
    row_ids: tuple[str, ...] = ()
    resume: bool = False
    overwrite: bool = False
    raw_output_dir: Path | None = None
    evaluate_output_dir: Path | None = None
    strict: bool = False
    dry_run: bool = False
    allow_nonlocal_endpoint: bool = False


class ChatClient(Protocol):
    def complete(
        self, *, messages: list[dict[str, str]], model: str, temperature: float,
        top_p: float | None, max_tokens: int, timeout: float,
    ) -> str: ...


class OpenAIChatClient:
    def __init__(self, endpoint: str, api_key: str | None = None):
        self.endpoint = endpoint
        self.api_key = api_key

    def complete(
        self, *, messages: list[dict[str, str]], model: str, temperature: float,
        top_p: float | None, max_tokens: int, timeout: float,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        http_request = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise RuntimeError(f"endpoint request failed with HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, OSError, UnicodeError) as exc:
            raise RuntimeError(f"endpoint request failed: {type(exc).__name__}") from exc
        try:
            result = json.loads(body)
            content = result["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("endpoint response did not contain choices[0].message.content") from exc
        if not isinstance(content, str):
            raise RuntimeError("endpoint message content must be a string")
        return content


def parse_model_output(raw: str) -> ParseResult:
    text = raw.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    else:
        if not isinstance(value, dict):
            return ParseResult(None, "parse_failed", "model output JSON must be an object, not an array or scalar")
        if "answer" in value:
            return ParseResult(None, "parse_failed", "model returned a full candidate row instead of answer content")
        return ParseResult(value, "parsed_json")

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            if "answer" in candidate:
                return ParseResult(None, "parse_failed", "model returned a full candidate row instead of answer content")
            return ParseResult(candidate, "extracted_json")
    return ParseResult(None, "parse_failed", "model output did not contain a JSON answer object")


def validate_endpoint(endpoint: str, allow_nonlocal: bool = False) -> str:
    parsed = parse.urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("endpoint URL must not contain credentials")
    if parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1/chat/completions":
        raise ValueError("endpoint must target /v1/chat/completions without query or fragment")
    host = parsed.hostname.lower()
    if host not in LOCAL_HOSTS and not allow_nonlocal:
        raise ValueError(f"non-local endpoint requires --allow-nonlocal-endpoint: {host}")
    return host


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


def safe_raw_output_path(directory: Path, row_id: str) -> Path:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", row_id).strip("._-") or "row"
    digest = hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:10]
    return directory / f"{stem[:80]}-{digest}.txt"


def _fallback_answer(row: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "schema_version": "rtl_answer_v0.1",
        "task_type": row.get("task_family"),
        "runner_status": status,
    }


def validate_candidate_answer(row: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    return _candidate_validation_errors(row, answer)


def _report_paths(output: Path) -> tuple[Path, Path]:
    return (
        output.with_name(f"{output.stem}.report.json"),
        output.with_name(f"{output.stem}.report.md"),
    )


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_text_atomic(path: Path, content: str) -> None:
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


def _write_reports(report: dict[str, Any], output: Path) -> None:
    json_path, markdown_path = _report_paths(output)
    _write_text_atomic(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    failed = report.get("failed_rows", [])
    failed_rows = "\n".join(
        f"| `{item['id']}` | {item['parse_status']} | {item['validation_status']} | {item['error']} |"
        for item in failed
    ) or "| none | | | |"
    evaluation = report.get("evaluation")
    evaluation_text = f"\n- Evaluation output: `{evaluation['output_dir']}`\n- Mean score: {evaluation['mean_score']}\n" if evaluation else ""
    next_command = (
        f"python scripts/eval/evaluate_answers.py --dataset {report['dataset']} "
        f"--candidates {report['output']} --output-dir <evaluation-output-dir> --json"
    )
    _write_text_atomic(markdown_path, f"""# Model candidate run

## Settings

- Dataset: `{report['dataset']}`
- Output: `{report['output']}`
- Model: `{report['model']}`
- Endpoint host: `{report['endpoint_host']}`
- Prompt template: `{report['prompt_template']}`
- Temperature: {report['generation_settings']['temperature']}
- Top-p: {report['generation_settings']['top_p']}
- Max tokens: {report['generation_settings']['max_tokens']}
- Timeout: {report['generation_settings']['timeout']}
- Retries: {report['generation_settings']['retries']}
- Dry run: {report['dry_run']}
{evaluation_text}
## Counts

- Attempted: {report['attempted_rows']}
- Written: {report['written_rows']}
- Skipped: {report['skipped_rows']}
- Parse statuses: `{json.dumps(report['parse_status_counts'], sort_keys=True)}`
- Validation statuses: `{json.dumps(report['validation_status_counts'], sort_keys=True)}`

## Failed rows

| Row | Parse | Validation | Error |
|---|---|---|---|
{failed_rows}

## Evaluate

```bash
{next_command}
```

The deterministic evaluator is a heuristic check, not proof of RTL correctness. No RTL or tool output is executed by this runner.
""")


def _base_report(config: RunnerConfig, host: str) -> dict[str, Any]:
    return {
        "ok": False,
        "dataset": str(config.dataset),
        "output": str(config.output),
        "model": config.model,
        "endpoint_host": host,
        "prompt_template": config.prompt_template,
        "prompt_version": PROMPT_VERSION,
        "generation_settings": {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
            "retries": config.retries,
        },
        "dry_run": config.dry_run,
        "attempted_rows": 0,
        "written_rows": 0,
        "skipped_rows": 0,
        "parse_status_counts": {},
        "validation_status_counts": {},
        "failed_rows": [],
        "errors": [],
        "warnings": [],
    }


def run_model_candidates(config: RunnerConfig, client: ChatClient | None = None) -> tuple[dict[str, Any], int]:
    try:
        host = validate_endpoint(config.endpoint, config.allow_nonlocal_endpoint)
    except ValueError as exc:
        report = _base_report(config, "invalid")
        report["errors"].append(str(exc))
        return report, 1
    report = _base_report(config, host)
    errors = report["errors"]
    if not config.model.strip():
        errors.append("model must be non-empty")
    if config.prompt_template not in SUPPORTED_PROMPT_TEMPLATES:
        errors.append(f"unsupported prompt template: {config.prompt_template}")
    if config.resume and config.overwrite:
        errors.append("--resume and --overwrite are mutually exclusive")
    if config.limit is not None and config.limit <= 0:
        errors.append("limit must be greater than zero")
    if config.max_tokens <= 0 or config.timeout <= 0 or config.retries < 0:
        errors.append("max-tokens and timeout must be positive; retries must be non-negative")
    if not 0.0 <= config.temperature <= 2.0 or (config.top_p is not None and not 0.0 < config.top_p <= 1.0):
        errors.append("temperature must be between 0 and 2; top-p must be in (0, 1]")
    _validate_directory(config.output.parent, "output directory", config.dataset, errors)
    _validate_managed_file(config.output, "output", errors)
    if config.raw_output_dir is not None:
        _validate_directory(config.raw_output_dir, "raw output directory", config.dataset, errors)
    if config.evaluate_output_dir is not None:
        _validate_directory(config.evaluate_output_dir, "evaluation output directory", config.dataset, errors)
        for filename in EVALUATION_FILES:
            _validate_managed_file(
                config.evaluate_output_dir / filename,
                f"evaluation output {filename}",
                errors,
            )
    if (
        config.raw_output_dir is not None
        and config.evaluate_output_dir is not None
        and _paths_overlap(config.raw_output_dir, config.evaluate_output_dir)
    ):
        errors.append("raw and evaluation output directories must not overlap")
    for directory, label in (
        (config.raw_output_dir, "raw output directory"),
        (config.evaluate_output_dir, "evaluation output directory"),
    ):
        if directory is not None and _is_within(config.output, directory):
            errors.append(f"output must not be inside {label}")
    if config.output.resolve() == config.dataset.resolve():
        errors.append("output must not overwrite the dataset input")
    output_exists = config.output.exists() or config.output.is_symlink()
    if output_exists and not config.resume and not config.overwrite:
        errors.append(f"output already exists; use --resume or --overwrite: {config.output}")
    if config.resume and not output_exists:
        errors.append(f"resume output not found: {config.output}")
    report_json, report_md = _report_paths(config.output)
    for report_path in (report_json, report_md):
        _validate_managed_file(report_path, "report path", errors)
    if not config.resume and not config.overwrite and not output_exists:
        for report_path in (report_json, report_md):
            if report_path.exists() or report_path.is_symlink():
                errors.append(f"report already exists; use --overwrite: {report_path}")
    if errors:
        return report, 1

    dataset_rows, dataset_errors = load_dataset_rows(config.dataset)
    if dataset_errors:
        errors.extend(dataset_errors)
        return report, 1
    selected = dataset_rows
    if config.row_ids:
        requested = set(config.row_ids)
        available = {str(row["id"]) for row in dataset_rows}
        missing = sorted(requested - available)
        if missing:
            errors.append(f"requested row IDs not found: {', '.join(missing)}")
            return report, 1
        selected = [row for row in selected if row["id"] in requested]
    if config.limit is not None:
        selected = selected[:config.limit]
    if config.raw_output_dir is not None:
        for row in selected:
            _validate_managed_file(
                safe_raw_output_path(config.raw_output_dir, str(row["id"])),
                f"raw output for {row['id']}",
                errors,
            )
        if errors:
            return report, 1

    existing_rows: list[dict[str, Any]] = []
    completed_ids: set[str] = set()
    if config.resume:
        loaded, problems = load_jsonl(config.output)
        if problems:
            errors.extend(problem.message for problem in problems)
            return report, 1
        for line, row in loaded:
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id or not isinstance(row.get("answer"), dict):
                errors.append(f"line {line}: existing candidate must contain string id and object answer")
                continue
            if row_id in completed_ids:
                errors.append(f"line {line}: duplicate candidate id {row_id}")
                continue
            completed_ids.add(row_id)
            existing_rows.append(row)
        if errors:
            return report, 1

    pending = [row for row in selected if row["id"] not in completed_ids]
    report["skipped_rows"] = len(selected) - len(pending)
    active_client: ChatClient | None = client
    if pending and not config.dry_run and active_client is None:
        token: str | None = None
        if config.api_key_env:
            token = os.environ.get(config.api_key_env)
            if token is None:
                errors.append(f"API key environment variable is not set: {config.api_key_env}")
                return report, 1
        active_client = OpenAIChatClient(config.endpoint, token)
    generated: list[dict[str, Any]] = []
    parse_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    endpoint_failed = False

    for row in pending:
        row_id = str(row["id"])
        messages = build_prompt(row, config.prompt_template)
        attempts = 0
        raw: str | None = None
        failure: str | None = None
        if config.dry_run:
            parsed = ParseResult(None, "dry_run")
        else:
            for attempts in range(1, config.retries + 2):
                try:
                    assert active_client is not None
                    raw = active_client.complete(
                        messages=messages,
                        model=config.model,
                        temperature=config.temperature,
                        top_p=config.top_p,
                        max_tokens=config.max_tokens,
                        timeout=config.timeout,
                    )
                    break
                except (RuntimeError, OSError, TimeoutError) as exc:
                    failure = str(exc)
            if raw is None:
                endpoint_failed = True
                parsed = ParseResult(None, "endpoint_failed", failure or "endpoint request failed")
            else:
                parsed = parse_model_output(raw)

        raw_path: Path | None = None
        if raw is not None and config.raw_output_dir is not None:
            config.raw_output_dir.mkdir(parents=True, exist_ok=True)
            raw_path = safe_raw_output_path(config.raw_output_dir, row_id)
            _write_text_atomic(raw_path, raw)
        answer = parsed.answer or _fallback_answer(row, parsed.status)
        if config.dry_run:
            validation_status = "not_validated"
            validation_errors: list[str] = []
        elif parsed.answer is None:
            validation_status = "candidate_invalid"
            validation_errors = [parsed.error or parsed.status]
        else:
            validation_errors = validate_candidate_answer(row, answer)
            validation_status = "candidate_valid" if not validation_errors else "candidate_invalid"
        metadata = {
            "model": config.model,
            "runner": "run_model_candidates.py",
            "prompt_template": config.prompt_template,
            "prompt_version": PROMPT_VERSION,
            "endpoint_host": host,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
            "retries": config.retries,
            "attempts": attempts,
            "parse_status": parsed.status,
            "validation_status": validation_status,
            "created_by": "model_runner",
            "raw_output_path": str(raw_path) if raw_path else None,
        }
        generated.append({"id": row_id, "answer": answer, "metadata": metadata})
        parse_counts[parsed.status] += 1
        validation_counts[validation_status] += 1
        if parsed.status in {"parse_failed", "endpoint_failed"} or validation_status == "candidate_invalid":
            report["failed_rows"].append({
                "id": row_id,
                "parse_status": parsed.status,
                "validation_status": validation_status,
                "error": "; ".join(validation_errors[:3]) or parsed.error or "unknown failure",
            })

    _write_jsonl_atomic(config.output, existing_rows + generated)
    report["attempted_rows"] = len(pending)
    report["written_rows"] = len(generated)
    report["parse_status_counts"] = dict(sorted(parse_counts.items()))
    report["validation_status_counts"] = dict(sorted(validation_counts.items()))
    if endpoint_failed:
        errors.append("one or more endpoint requests failed")
    parse_or_validation_failed = bool(report["failed_rows"])
    if parse_or_validation_failed and not config.strict and not endpoint_failed:
        report["warnings"].append("one or more rows had parse or validation failures")
    if config.strict and parse_or_validation_failed:
        errors.append("strict mode fails on parse or validation errors")

    if config.evaluate_output_dir is not None:
        candidate_result = load_candidate_answers(config.output)
        if candidate_result.errors:
            errors.extend(candidate_result.errors)
        else:
            evaluation, evaluation_code = evaluate_dataset(
                dataset_rows,
                candidate_result.candidates,
                candidate_result.rows,
                config.evaluate_output_dir,
                config.dataset,
                config.output,
                strict=config.strict,
            )
            report["evaluation"] = evaluation
            if evaluation_code:
                errors.append("evaluation failed: " + "; ".join(evaluation["errors"] or ["unknown error"]))
    report["ok"] = not errors
    _write_reports(report, config.output)
    return report, 0 if report["ok"] else 1
