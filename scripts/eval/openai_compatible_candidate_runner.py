"""Generate evaluator-ready candidates with an OpenAI-compatible chat endpoint."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol
from urllib import error, parse, request

from scripts.dataset.io_utils import load_jsonl
from scripts.eval.evaluator import load_dataset_rows


DEFAULT_API_KEY_ENV = "RTLSPEC_EVAL_API_KEY"
DEFAULT_MODEL = "active-model"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TIMEOUT = 120.0
DEFAULT_RETRIES = 2


@dataclass(frozen=True)
class OpenAICompatibleRunnerConfig:
    dataset: Path
    output: Path
    base_url: str
    model: str = DEFAULT_MODEL
    api_key_env: str = DEFAULT_API_KEY_ENV
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    limit: int | None = None
    resume: bool = False
    timeout: float = DEFAULT_TIMEOUT
    raw_output_dir: Path | None = None
    retries: int = DEFAULT_RETRIES
    fail_fast: bool = False
    schema_reminder: str | None = None
    schema_reminder_file: Path | None = None
    response_format_json: bool = False


class ChatCompletionClient(Protocol):
    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: float,
        response_format_json: bool,
    ) -> str: ...


class OpenAICompatibleChatClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        timeout: float,
        response_format_json: bool,
    ) -> str:
        endpoint = _completion_endpoint(self.base_url)
        payload = build_chat_payload(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=response_format_json,
        )
        http_request = request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise RuntimeError(f"chat completion request failed with HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, OSError, UnicodeError) as exc:
            raise RuntimeError(f"chat completion request failed: {type(exc).__name__}") from exc
        return _extract_chat_content(body)


def _completion_endpoint(base_url: str) -> str:
    parsed = parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query string or fragment")
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return normalized + "/chat/completions"


def _extract_chat_content(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("chat completion response was not valid JSON") from exc
    try:
        message = payload["choices"][0]["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("chat completion response did not contain choices[0].message.content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "".join(parts)
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=False)
    raise RuntimeError("chat completion response content must be a string or text parts")


def build_chat_payload(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    response_format_json: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}
    return payload


def load_schema_reminder_text(
    schema_reminder: str | None = None,
    schema_reminder_file: Path | None = None,
) -> str | None:
    reminders: list[str] = []
    if schema_reminder is not None and schema_reminder.strip():
        reminders.append(schema_reminder.strip())
    if schema_reminder_file is not None:
        try:
            file_text = schema_reminder_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"could not read schema reminder file: {exc}") from exc
        if not file_text.strip():
            raise ValueError(f"schema reminder file is empty: {schema_reminder_file}")
        reminders.append(file_text.strip())
    if not reminders:
        return None
    return "\n\n".join(reminders)


def build_request_messages(row: dict[str, Any], schema_reminder_text: str | None = None) -> list[dict[str, str]]:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("dataset row must contain at least system and user messages")
    request_messages: list[dict[str, str]] = []
    for index in (0, 1):
        message = messages[index]
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object")
        role = message.get("role")
        if not isinstance(role, str) or not role:
            raise ValueError(f"messages[{index}].role must be a non-empty string")
        content = message.get("content")
        if isinstance(content, str):
            serialized = content
        else:
            serialized = json.dumps(content, ensure_ascii=False, indent=2)
        if index == 0 and schema_reminder_text is not None:
            serialized = serialized.rstrip() + "\n\n" + schema_reminder_text.strip()
        request_messages.append({"role": role, "content": serialized})
    return request_messages


def parse_candidate_answer_text(raw_text: str) -> tuple[dict[str, Any], str | None]:
    candidates = _candidate_parse_texts(raw_text)
    object_error: str | None = None
    for candidate in candidates:
        parsed = _try_parse_object(candidate)
        if parsed is not None:
            return parsed, None
        if object_error is None and _loads_non_object(candidate):
            object_error = "model output JSON must be an object"
    for candidate in candidates:
        parsed = _extract_first_json_object(candidate)
        if parsed is not None:
            return parsed, None
    error_message = object_error or "model output did not contain a JSON object"
    return {
        "schema_version": "parse_error",
        "raw_text": raw_text,
        "error": error_message,
    }, error_message


def _candidate_parse_texts(raw_text: str) -> list[str]:
    texts: list[str] = []

    def add(value: str) -> None:
        stripped = value.strip()
        if stripped and stripped not in texts:
            texts.append(stripped)

    add(raw_text)
    stripped = _strip_outer_markdown_fence(raw_text)
    if stripped != raw_text.strip():
        add(stripped)
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw_text, flags=re.IGNORECASE | re.DOTALL):
        add(match.group(1))
    return texts


def _strip_outer_markdown_fence(raw_text: str) -> str:
    match = re.match(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", raw_text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        return raw_text.strip()
    return match.group(1).strip()


def _try_parse_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _loads_non_object(text: str) -> bool:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return False
    return not isinstance(value, dict)


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def safe_raw_output_path(directory: Path, row_id: str) -> Path:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", row_id).strip("._-") or "row"
    digest = hashlib.sha256(row_id.encode("utf-8")).hexdigest()[:10]
    return directory / f"{stem[:80]}-{digest}.txt"


def run_openai_compatible_candidates(
    config: OpenAICompatibleRunnerConfig,
    client: ChatCompletionClient | None = None,
) -> tuple[dict[str, Any], int]:
    summary = {
        "ok": False,
        "dataset": str(config.dataset),
        "output": str(config.output),
        "base_url": config.base_url,
        "model": config.model,
        "api_key_env": config.api_key_env,
        "raw_output_dir": str(config.raw_output_dir) if config.raw_output_dir is not None else None,
        "selected_rows": 0,
        "skipped_rows": 0,
        "written_rows": 0,
        "candidate_rows": 0,
        "parse_error_rows": 0,
        "api_error_rows": 0,
        "parse_error_ids": [],
        "api_error_ids": [],
        "stopped_early": False,
        "schema_reminder_enabled": False,
        "response_format_json": config.response_format_json,
        "errors": [],
        "warnings": [],
    }
    errors: list[str] = summary["errors"]
    warnings: list[str] = summary["warnings"]

    if not config.base_url.strip():
        errors.append("base URL must be non-empty")
    else:
        try:
            _completion_endpoint(config.base_url)
        except ValueError as exc:
            errors.append(str(exc))
    if not config.model.strip():
        errors.append("model must be non-empty")
    if not config.api_key_env.strip():
        errors.append("API key environment variable name must be non-empty")
    if config.limit is not None and config.limit <= 0:
        errors.append("limit must be greater than zero")
    if config.max_tokens <= 0:
        errors.append("max_tokens must be greater than zero")
    if config.timeout <= 0:
        errors.append("timeout must be greater than zero")
    if config.retries < 0:
        errors.append("retries must be zero or greater")
    if not 0.0 <= config.temperature <= 2.0:
        errors.append("temperature must be between 0 and 2")
    if config.output.exists() and not config.resume:
        errors.append(f"output already exists; rerun with --resume or remove it first: {config.output}")
    if config.output.exists() and config.output.is_dir():
        errors.append(f"output must be a file path, not a directory: {config.output}")
    if config.raw_output_dir is not None and config.raw_output_dir.exists() and not config.raw_output_dir.is_dir():
        errors.append(f"raw output directory must be a directory path: {config.raw_output_dir}")
    try:
        schema_reminder_text = load_schema_reminder_text(
            config.schema_reminder,
            config.schema_reminder_file,
        )
    except ValueError as exc:
        errors.append(str(exc))
        schema_reminder_text = None
    else:
        summary["schema_reminder_enabled"] = schema_reminder_text is not None
    if errors:
        return summary, 1

    dataset_rows, dataset_errors = load_dataset_rows(config.dataset)
    if dataset_errors:
        errors.extend(dataset_errors)
        return summary, 1
    selected_rows = dataset_rows[:config.limit] if config.limit is not None else dataset_rows
    summary["selected_rows"] = len(selected_rows)

    existing_rows: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    if config.resume and config.output.exists():
        existing_rows, existing_ids, load_errors = _load_existing_candidates(config.output)
        if load_errors:
            errors.extend(load_errors)
            return summary, 1

    pending_rows = [row for row in selected_rows if str(row["id"]) not in existing_ids]
    summary["skipped_rows"] = len(selected_rows) - len(pending_rows)
    if pending_rows:
        api_key = os.environ.get(config.api_key_env)
        if api_key is None:
            errors.append(f"API key environment variable is not set: {config.api_key_env}")
            return summary, 1
        if not api_key.strip():
            errors.append(f"API key environment variable is empty: {config.api_key_env}")
            return summary, 1
        active_client = client or OpenAICompatibleChatClient(config.base_url, api_key)
    else:
        active_client = client

    config.output.parent.mkdir(parents=True, exist_ok=True)
    if config.raw_output_dir is not None:
        config.raw_output_dir.mkdir(parents=True, exist_ok=True)

    mode = "a" if config.resume and config.output.exists() else "w"
    written_rows = 0
    parse_error_ids: list[str] = summary["parse_error_ids"]
    api_error_ids: list[str] = summary["api_error_ids"]
    with config.output.open(mode, encoding="utf-8", newline="\n") as handle:
        for row in pending_rows:
            row_id = str(row["id"])
            messages = build_request_messages(row, schema_reminder_text=schema_reminder_text)
            raw_text, attempts, api_error = _request_candidate_text(
                active_client,
                model=config.model,
                messages=messages,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                timeout=config.timeout,
                retries=config.retries,
                response_format_json=config.response_format_json,
            )
            raw_output_path: Path | None = None
            if raw_text is not None and config.raw_output_dir is not None:
                raw_output_path = safe_raw_output_path(config.raw_output_dir, row_id)
                raw_output_path.write_text(raw_text, encoding="utf-8")
            if api_error is None:
                answer, parse_error = parse_candidate_answer_text(raw_text or "")
                if parse_error is not None:
                    parse_error_ids.append(row_id)
            else:
                answer = {
                    "schema_version": "api_error",
                    "error": api_error,
                    "raw_text": None,
                }
                parse_error = None
                api_error_ids.append(row_id)
            candidate_row = {
                "id": row_id,
                "answer": answer,
                "metadata": {
                    "model": config.model,
                    "base_url": config.base_url,
                    "temperature": config.temperature,
                    "max_tokens": config.max_tokens,
                    "raw_output_path": str(raw_output_path) if raw_output_path is not None else None,
                    "parse_error": parse_error,
                    "attempts": attempts,
                },
            }
            handle.write(json.dumps(candidate_row, ensure_ascii=False, separators=(",", ":")) + "\n")
            written_rows += 1
            if api_error is not None and config.fail_fast:
                summary["stopped_early"] = True
                errors.append(f"API request failed for row {row_id}: {api_error}")
                break

    summary["written_rows"] = written_rows
    summary["candidate_rows"] = len(existing_rows) + written_rows
    summary["parse_error_rows"] = len(parse_error_ids)
    summary["api_error_rows"] = len(api_error_ids)
    if parse_error_ids:
        warnings.append(f"{len(parse_error_ids)} row(s) produced parse_error answers")
    if api_error_ids and not config.fail_fast:
        warnings.append(f"{len(api_error_ids)} row(s) produced api_error answers")
    summary["ok"] = not errors
    return summary, 0 if summary["ok"] else 1


def _request_candidate_text(
    client: ChatCompletionClient | None,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: float,
    retries: int,
    response_format_json: bool,
) -> tuple[str | None, int, str | None]:
    if client is None:
        raise RuntimeError("chat completion client is required when rows are pending")
    last_error: str | None = None
    attempts = 0
    for attempts in range(1, retries + 2):
        try:
            raw_text = client.complete(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format_json=response_format_json,
            )
        except (RuntimeError, OSError, TimeoutError) as exc:
            last_error = str(exc)
            continue
        return raw_text, attempts, None
    return None, attempts, last_error or "chat completion request failed"


def _load_existing_candidates(path: Path) -> tuple[list[dict[str, Any]], set[str], list[str]]:
    if not path.exists():
        return [], set(), []
    if path.stat().st_size == 0:
        return [], set(), []
    loaded, problems = load_jsonl(path)
    errors = [problem.message for problem in problems]
    rows: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for line, row in loaded:
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            errors.append(f"line {line}: existing candidate id must be a non-empty string")
            continue
        if row_id in existing_ids:
            errors.append(f"line {line}: duplicate candidate id {row_id}")
            continue
        existing_ids.add(row_id)
        rows.append(row)
    return rows, existing_ids, errors
