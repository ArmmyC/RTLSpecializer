#!/usr/bin/env python3
"""Check whether the current runtime can support the Qwen2.5-Coder-7B LoRA pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.finetune.training_utils import (
    DEFAULT_DATASET_DIR,
    DEFAULT_EXPECTED_GPU_SUBSTRING,
    collect_environment_summary,
)


def _print_text(summary: dict[str, object]) -> None:
    torch_summary = summary["torch"]
    print("Training environment check passed." if summary["ok"] else "Training environment check failed.")
    print(f"Python: {summary['python']['version']} ({summary['python']['implementation']})")
    print(f"Executable: {summary['python']['executable']}")
    print(f"Host: {summary['host']['hostname']}")
    print(f"Working dir: {summary['host']['working_dir']}")
    print(f"CUDA available: {torch_summary['cuda_available']}")
    print(f"Device count: {torch_summary['device_count']}")
    for device in torch_summary["devices"]:
        print(f"GPU {device['index']}: {device['name']} ({device['total_memory_gb']} GiB)")
    if summary["dataset_dir"] is not None:
        print(f"Dataset dir visible: {summary['dataset_dir']['exists']}")
        print(f"Dataset dir path: {summary['dataset_dir']['path']}")
    if summary["errors"]:
        print("Errors:")
        for error in summary["errors"]:
            print(f"- {error}")
    if summary["warnings"]:
        print("Warnings:")
        for warning in summary["warnings"]:
            print(f"- {warning}")


def check_training_environment(
    dataset_dir: Path | None = DEFAULT_DATASET_DIR,
    *,
    expected_gpu_substring: str | None = DEFAULT_EXPECTED_GPU_SUBSTRING,
) -> tuple[dict[str, object], int]:
    return collect_environment_summary(
        dataset_dir=dataset_dir,
        expected_gpu_substring=expected_gpu_substring,
        require_cuda=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--expected-gpu-substring", default=DEFAULT_EXPECTED_GPU_SUBSTRING)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    expected_gpu_substring = args.expected_gpu_substring or None
    summary, code = check_training_environment(
        args.dataset_dir,
        expected_gpu_substring=expected_gpu_substring,
    )
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        _print_text(summary)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
