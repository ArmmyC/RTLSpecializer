#!/usr/bin/env python3
"""Shared helpers for fine-tune environment checks and LoRA training."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import importlib
import json
from pathlib import Path
import platform
import socket
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.finetune.check_finetune_dataset import check_finetune_dataset


DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
DEFAULT_DATASET_DIR = Path("outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical")
DEFAULT_OUTPUT_DIR = Path("outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora")
DEFAULT_EXPECTED_GPU_SUBSTRING = "L40"
DEFAULT_TARGET_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)
REQUIRED_PACKAGES = (
    "torch",
    "transformers",
    "datasets",
    "accelerate",
    "peft",
    "trl",
    "safetensors",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stringify_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return _canonical_json(content)


def normalize_chat_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise TypeError(f"messages[{index}] must be an object")
        role = message.get("role")
        if not isinstance(role, str) or not role:
            raise ValueError(f"messages[{index}].role must be a non-empty string")
        normalized.append({
            "role": role,
            "content": stringify_message_content(message.get("content")),
        })
    return normalized


def format_example_for_chat_template(example: dict[str, Any], tokenizer: Any) -> str:
    messages = example.get("messages")
    if not isinstance(messages, list):
        raise ValueError("example must contain a messages list")
    normalized_messages = normalize_chat_messages(messages)
    return tokenizer.apply_chat_template(
        normalized_messages,
        tokenize=False,
        add_generation_prompt=False,
    )


def _output_dir_errors(
    output_dir: Path,
    *,
    overwrite_output_dir: bool,
    resume_from_checkpoint: Path | None,
) -> list[str]:
    errors: list[str] = []
    if output_dir.exists() and output_dir.is_symlink():
        errors.append(f"--output-dir must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        errors.append(f"--output-dir exists and is not a directory: {output_dir}")
    if resume_from_checkpoint is not None:
        if not resume_from_checkpoint.exists():
            errors.append(f"--resume-from-checkpoint not found: {resume_from_checkpoint}")
        elif not resume_from_checkpoint.is_dir():
            errors.append(f"--resume-from-checkpoint must be a directory: {resume_from_checkpoint}")
    if (
        output_dir.exists()
        and output_dir.is_dir()
        and any(output_dir.iterdir())
        and not overwrite_output_dir
        and resume_from_checkpoint is None
    ):
        errors.append(
            "--output-dir already exists and is not empty; rerun with --overwrite-output-dir "
            "or point --resume-from-checkpoint at an existing checkpoint"
        )
    return errors


def preflight_training_run(
    dataset_dir: Path,
    output_dir: Path,
    *,
    overwrite_output_dir: bool = False,
    resume_from_checkpoint: Path | None = None,
) -> tuple[dict[str, Any], int]:
    errors = _output_dir_errors(
        output_dir,
        overwrite_output_dir=overwrite_output_dir,
        resume_from_checkpoint=resume_from_checkpoint,
    )
    dataset_summary, dataset_code = check_finetune_dataset(dataset_dir)
    warnings = list(dataset_summary.get("warnings", []))

    alias_errors: list[str] = []
    schema_aliases = dataset_summary.get("schema_aliases", {})
    user_aliases = schema_aliases.get("user", {})
    assistant_aliases = schema_aliases.get("assistant", {})
    if user_aliases or assistant_aliases:
        alias_errors.append(
            "dataset_dir must be a canonical fine-tune export with zero schema aliases; "
            "rerun scripts/finetune/export_canonical_finetune_dataset.py first"
        )

    if dataset_code != 0:
        errors.extend(dataset_summary.get("errors", []))
    errors.extend(alias_errors)

    summary = {
        "ok": not errors,
        "dataset_dir": str(dataset_dir),
        "output_dir": str(output_dir),
        "resume_from_checkpoint": str(resume_from_checkpoint) if resume_from_checkpoint is not None else None,
        "overwrite_output_dir": overwrite_output_dir,
        "dataset_check": dataset_summary,
        "errors": errors,
        "warnings": warnings,
    }
    return summary, 0 if summary["ok"] else 1


def collect_package_import_state(
    package_names: Sequence[str] = REQUIRED_PACKAGES,
    *,
    importer: Callable[[str], Any] = importlib.import_module,
) -> tuple[dict[str, Any], dict[str, str | None], list[str]]:
    modules: dict[str, Any] = {}
    versions: dict[str, str | None] = {}
    errors: list[str] = []
    for package_name in package_names:
        try:
            module = importer(package_name)
        except Exception as exc:
            versions[package_name] = None
            errors.append(f"could not import {package_name}: {exc}")
            continue
        modules[package_name] = module
        version = getattr(module, "__version__", None)
        versions[package_name] = str(version) if version is not None else None
    return modules, versions, errors


def summarize_torch_environment(torch_module: Any) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    summary: dict[str, Any] = {
        "version": getattr(torch_module, "__version__", None),
        "cuda_available": False,
        "bf16_supported": False,
        "device_count": 0,
        "devices": [],
    }
    try:
        cuda_available = bool(torch_module.cuda.is_available())
    except Exception as exc:
        errors.append(f"torch.cuda.is_available() failed: {exc}")
        return summary, errors

    summary["cuda_available"] = cuda_available
    if not cuda_available:
        return summary, errors

    try:
        summary["device_count"] = int(torch_module.cuda.device_count())
    except Exception as exc:
        errors.append(f"torch.cuda.device_count() failed: {exc}")
        return summary, errors

    try:
        summary["bf16_supported"] = bool(torch_module.cuda.is_bf16_supported())
    except Exception as exc:
        errors.append(f"torch.cuda.is_bf16_supported() failed: {exc}")

    for index in range(summary["device_count"]):
        try:
            props = torch_module.cuda.get_device_properties(index)
            name = torch_module.cuda.get_device_name(index)
        except Exception as exc:
            errors.append(f"torch.cuda device query failed for index {index}: {exc}")
            continue
        summary["devices"].append({
            "index": index,
            "name": name,
            "total_memory_gb": round(props.total_memory / 1024**3, 2),
        })
    return summary, errors


def expected_gpu_errors(torch_summary: dict[str, Any], expected_gpu_substring: str | None) -> list[str]:
    if not expected_gpu_substring:
        return []
    expected = expected_gpu_substring.lower()
    names = [
        str(device.get("name", ""))
        for device in torch_summary.get("devices", [])
        if isinstance(device, dict)
    ]
    if any(expected in name.lower() for name in names):
        return []
    return [
        f"expected a visible GPU containing {expected_gpu_substring!r}, found {names or ['<none>']}"
    ]


def collect_environment_summary(
    *,
    dataset_dir: Path | None = None,
    expected_gpu_substring: str | None = DEFAULT_EXPECTED_GPU_SUBSTRING,
    require_cuda: bool = True,
    importer: Callable[[str], Any] = importlib.import_module,
) -> tuple[dict[str, Any], int]:
    modules, package_versions, errors = collect_package_import_state(importer=importer)
    warnings: list[str] = []

    torch_summary = {
        "version": None,
        "cuda_available": False,
        "bf16_supported": False,
        "device_count": 0,
        "devices": [],
    }
    if "torch" in modules:
        torch_summary, torch_errors = summarize_torch_environment(modules["torch"])
        errors.extend(torch_errors)

    if require_cuda and not torch_summary.get("cuda_available", False):
        errors.append("CUDA is not available in the current runtime")
    errors.extend(expected_gpu_errors(torch_summary, expected_gpu_substring))

    dataset_summary: dict[str, Any] | None = None
    if dataset_dir is not None:
        dataset_summary = {
            "path": str(dataset_dir),
            "exists": dataset_dir.exists(),
            "is_dir": dataset_dir.is_dir(),
        }
        if not dataset_summary["exists"]:
            errors.append(f"dataset directory not found from current runtime: {dataset_dir}")
        elif not dataset_summary["is_dir"]:
            errors.append(f"dataset path is not a directory: {dataset_dir}")

    summary = {
        "ok": not errors,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "working_dir": str(Path.cwd()),
        },
        "packages": package_versions,
        "torch": torch_summary,
        "dataset_dir": dataset_summary,
        "errors": errors,
        "warnings": warnings,
    }
    return summary, 0 if summary["ok"] else 1
