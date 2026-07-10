#!/usr/bin/env python3
"""Concrete TRL/PEFT LoRA training entrypoint for the Qwen2.5-Coder-7B pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.finetune.training_utils import (
    DEFAULT_BASE_MODEL,
    DEFAULT_DATASET_DIR,
    DEFAULT_EXPECTED_GPU_SUBSTRING,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TARGET_MODULES,
    collect_environment_summary,
    format_example_for_chat_template,
    preflight_training_run,
)


def _load_runtime() -> dict[str, Any]:
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    return {
        "torch": torch,
        "load_dataset": load_dataset,
        "LoraConfig": LoraConfig,
        "AutoModelForCausalLM": AutoModelForCausalLM,
        "AutoTokenizer": AutoTokenizer,
        "SFTConfig": SFTConfig,
        "SFTTrainer": SFTTrainer,
    }


def _trainable_parameter_summary(model: Any) -> dict[str, Any]:
    total = 0
    trainable = 0
    for parameter in model.parameters():
        count = int(parameter.numel())
        total += count
        if parameter.requires_grad:
            trainable += count
    percentage = round((trainable / total) * 100, 4) if total else 0.0
    return {
        "total_parameters": total,
        "trainable_parameters": trainable,
        "trainable_percentage": percentage,
    }


def _parse_max_steps(value: str) -> int:
    parsed = int(value)
    if parsed == 0 or parsed < -1:
        raise argparse.ArgumentTypeError("--max-steps must be -1 or a positive integer")
    return parsed


def _render_dataset(dataset: Any, tokenizer: Any) -> Any:
    """Remove structured messages after rendering so TRL treats rows as plain text."""

    return dataset.map(
        lambda example: {"text": format_example_for_chat_template(example, tokenizer)},
        remove_columns=dataset.column_names,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--resume-from-checkpoint", type=Path)
    parser.add_argument("--overwrite-output-dir", action="store_true")
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--learning-rate", type=float, default=1.0e-4)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--eval-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=16)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--save-steps", type=int, default=100)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--save-total-limit", type=int, default=2)
    parser.add_argument(
        "--max-steps",
        type=_parse_max_steps,
        default=-1,
        help="Override epochs with an exact positive update-step count; -1 uses --epochs.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--expected-gpu-substring", default=DEFAULT_EXPECTED_GPU_SUBSTRING)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def train_qwen2_5_coder_7b_lora(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    preflight_summary, preflight_code = preflight_training_run(
        args.dataset_dir,
        args.output_dir,
        overwrite_output_dir=args.overwrite_output_dir,
        resume_from_checkpoint=args.resume_from_checkpoint,
    )
    summary: dict[str, Any] = {
        "ok": False,
        "mode": "dry_run" if args.dry_run else "train",
        "base_model": args.base_model,
        "dataset_dir": str(args.dataset_dir),
        "output_dir": str(args.output_dir),
        "resume_from_checkpoint": str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None,
        "settings": {
            "max_length": args.max_length,
            "learning_rate": args.learning_rate,
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "eval_batch_size": args.eval_batch_size,
            "gradient_accumulation_steps": args.gradient_accumulation_steps,
            "logging_steps": args.logging_steps,
            "save_steps": args.save_steps,
            "eval_steps": args.eval_steps,
            "save_total_limit": args.save_total_limit,
            "max_steps": args.max_steps,
            "seed": args.seed,
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": args.lora_dropout,
            "target_modules": list(DEFAULT_TARGET_MODULES),
            "expected_gpu_substring": args.expected_gpu_substring,
        },
        "preflight": preflight_summary,
        "environment": None,
        "trainable_parameters": None,
        "artifacts": {},
        "errors": list(preflight_summary["errors"]),
        "warnings": list(preflight_summary["warnings"]),
    }
    if preflight_code != 0:
        return summary, 1

    if args.dry_run:
        summary["ok"] = True
        return summary, 0

    environment_summary, environment_code = collect_environment_summary(
        expected_gpu_substring=args.expected_gpu_substring or None,
        require_cuda=True,
    )
    summary["environment"] = environment_summary
    if environment_code != 0:
        summary["errors"].extend(environment_summary["errors"])
        summary["warnings"].extend(environment_summary["warnings"])
        return summary, 1

    runtime = _load_runtime()
    torch = runtime["torch"]
    use_bf16 = bool(torch.cuda.is_available() and torch.cuda.is_bf16_supported())
    torch_dtype = torch.bfloat16 if use_bf16 else torch.float16

    tokenizer = runtime["AutoTokenizer"].from_pretrained(
        args.base_model,
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model = runtime["AutoModelForCausalLM"].from_pretrained(
        args.base_model,
        torch_dtype=torch_dtype,
        trust_remote_code=True,
    )
    model.config.use_cache = False

    dataset = runtime["load_dataset"](
        "json",
        data_files={
            "train": str(args.dataset_dir / "train.jsonl"),
            "validation": str(args.dataset_dir / "validation.jsonl"),
        },
    )
    train_dataset = dataset["train"]
    validation_dataset = dataset["validation"]
    train_dataset = _render_dataset(train_dataset, tokenizer)
    validation_dataset = _render_dataset(validation_dataset, tokenizer)

    peft_config = runtime["LoraConfig"](
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=list(DEFAULT_TARGET_MODULES),
        bias="none",
        task_type="CAUSAL_LM",
    )
    trainer_args = runtime["SFTConfig"](
        output_dir=str(args.output_dir),
        learning_rate=args.learning_rate,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        eval_steps=args.eval_steps,
        eval_strategy="steps",
        save_strategy="steps",
        save_total_limit=args.save_total_limit,
        max_steps=args.max_steps,
        bf16=use_bf16,
        fp16=not use_bf16,
        gradient_checkpointing=True,
        report_to="none",
        seed=args.seed,
        max_length=args.max_length,
        dataset_text_field="text",
        packing=False,
        trust_remote_code=True,
    )
    trainer = runtime["SFTTrainer"](
        model=model,
        args=trainer_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
    )

    summary["trainable_parameters"] = _trainable_parameter_summary(trainer.model)

    train_result = trainer.train(
        resume_from_checkpoint=str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None
    )
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    trainer.save_state()

    train_metrics = dict(train_result.metrics)
    trainer.log_metrics("train", train_metrics)
    trainer.save_metrics("train", train_metrics)

    eval_metrics = trainer.evaluate()
    trainer.log_metrics("eval", eval_metrics)
    trainer.save_metrics("eval", eval_metrics)

    summary["ok"] = True
    summary["artifacts"] = {
        "model_dir": str(args.output_dir),
        "train_metrics_json": str(args.output_dir / "train_results.json"),
        "eval_metrics_json": str(args.output_dir / "eval_results.json"),
        "trainer_state_json": str(args.output_dir / "trainer_state.json"),
    }
    summary["train_metrics"] = train_metrics
    summary["eval_metrics"] = eval_metrics
    return summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    summary, code = train_qwen2_5_coder_7b_lora(args)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
