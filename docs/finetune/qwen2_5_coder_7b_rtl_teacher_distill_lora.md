# Qwen2.5-Coder-7B RTL teacher-distill LoRA pilot

This pilot targets:

- base model: `Qwen/Qwen2.5-Coder-7B-Instruct`
- source distill dataset: `data/distill/rtlcoder_synthetic_teacher_distill_v0_1`
- canonical training dataset: `outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical`
- hardware: NVIDIA L40-class GPU
- method: regular LoRA first, not QLoRA

Important constraints:

- The teacher-distill dataset is unreviewed.
- It is not approved.
- It is not golden.
- This pilot is for structured-output behavior and task grounding, not proof of RTL correctness.

If the currently hosted `active-model` baseline is not exactly `Qwen/Qwen2.5-Coder-7B-Instruct`, keep it only as a hosted baseline or teacher comparison. The before/after fine-tune comparison for this pilot should still use `Qwen/Qwen2.5-Coder-7B-Instruct`.

## 1. Check the source distill dataset first

Run the local checker before export/training:

```bash
python scripts/finetune/check_finetune_dataset.py \
  --dataset-dir data/distill/rtlcoder_synthetic_teacher_distill_v0_1 \
  --output-md data/reports/finetune/rtlcoder_synthetic_teacher_distill_dataset_check.md \
  --output-json data/reports/finetune/rtlcoder_synthetic_teacher_distill_dataset_check.json \
  --json
```

This verifies:

- `train.jsonl`, `validation.jsonl`, and `test.jsonl` exist
- each row keeps exact `system` / `user` / `assistant` message order
- user rows are accepted `rtl_task_v0.1` content, including known schema aliases
- assistant rows are accepted `rtl_answer_v0.1` content, including known schema aliases
- review and approval statuses remain pilot-safe

The checker should keep reporting aliases if they are present in the source teacher-distill package. That is expected input behavior, not a training-ready guarantee.

## 2. Export a canonical training copy

Before training, export a canonical dataset copy so the actual training rows use:

- `messages[1].content.schema_version = rtl_task_v0.1`
- `messages[2].content.schema_version = rtl_answer_v0.1`

```bash
python scripts/finetune/export_canonical_finetune_dataset.py \
  --dataset-dir data/distill/rtlcoder_synthetic_teacher_distill_v0_1 \
  --output-dir outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical \
  --json
```

This export rewrites only the canonical training copy. It does not mutate the original `data/distill/...` files.

Optionally re-run the checker on the canonical output:

```bash
python scripts/finetune/check_finetune_dataset.py \
  --dataset-dir outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical \
  --json
```

## 3. Primary config

Use:

```text
configs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora.yaml
```

Why this config:

- regular LoRA is the primary recommendation for L40-class hardware
- no 4-bit quantization by default
- training data points to the canonical fine-tune export, not the raw teacher-distill package
- `max_seq_length: 4096` is a safe pilot starting point
- `epochs: 1` limits early overfitting risk on the 1000-row set
- LoRA targets include Qwen-style attention and MLP projection modules

## 4. Option A: Axolotl-style LoRA command template

This is a template only. Exact keys can vary slightly by Axolotl version.

```bash
accelerate launch -m axolotl.cli.train \
  configs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora.yaml
```

Adjust batch size, accumulation, and data-format wiring to match the installed Axolotl release.

## 5. Option B: TRL / SFTTrainer-style LoRA template

This is a framework-template example, not a required repo script:

```python
from pathlib import Path
import json
import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

base_model = "Qwen/Qwen2.5-Coder-7B-Instruct"
dataset_dir = Path("outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical")
output_dir = "outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora"

tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    base_model,
    torch_dtype=torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16,
)

peft_config = LoraConfig(
    r=16,
    lora_alpha=32,
    lora_dropout=0.05,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    bias="none",
    task_type="CAUSAL_LM",
)

def format_row(example):
    messages = example["messages"]
    return {
        "text": tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    }

train_dataset = load_dataset("json", data_files=str(dataset_dir / "train.jsonl"))["train"].map(format_row)
validation_dataset = load_dataset("json", data_files=str(dataset_dir / "validation.jsonl"))["train"].map(format_row)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=train_dataset,
    eval_dataset=validation_dataset,
    peft_config=peft_config,
    args=SFTConfig(
        output_dir=output_dir,
        max_seq_length=4096,
        learning_rate=1e-4,
        num_train_epochs=1,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        bf16=torch.cuda.is_available() and torch.cuda.is_bf16_supported(),
        fp16=not (torch.cuda.is_available() and torch.cuda.is_bf16_supported()),
        logging_steps=10,
        save_steps=100,
        eval_steps=100,
        seed=42,
    ),
)

trainer.train()
trainer.save_model(output_dir)
```

## 6. QLoRA fallback only

Use QLoRA only if one of these is true:

- regular LoRA still hits CUDA out-of-memory
- you need much longer context, such as `8192+`
- you need a larger effective batch size on the same GPU
- the same GPU must serve other workloads during training

Fallback-only settings:

```yaml
method: qlora
quantization: 4bit
learning_rate: 2e-4
```

For a TRL-style fallback, the main differences are enabling 4-bit loading and using the higher fallback learning rate:

```python
from transformers import BitsAndBytesConfig

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16,
)
```

Do not treat QLoRA as the primary recommendation for this L40 pilot.

## 7. Evaluate before and after

Before training:

1. Run the exact base model `Qwen/Qwen2.5-Coder-7B-Instruct` on the held-out `test.jsonl`.
2. Evaluate that run with `scripts/eval/evaluate_answers.py`.

After training:

1. Run the LoRA-adapted model on the same held-out `test.jsonl`.
2. Evaluate it into a new run directory.
3. Compare runs using:

```bash
python scripts/eval/compare_eval_runs.py \
  --runs \
    data/eval/runs/rtlcoder_synthetic_rule_baseline \
    data/eval/runs/qwen2_5_coder_7b_base \
    data/eval/runs/qwen2_5_coder_7b_lora_pilot \
  --output-md data/reports/finetune/qwen2_5_coder_7b_run_comparison.md \
  --output-json data/reports/finetune/qwen2_5_coder_7b_run_comparison.json \
  --json
```

To inspect whether the model is actually identifying the synthetic bug rather than repeating a generic schema-safe answer:

```bash
python scripts/eval/inspect_candidate_differences.py \
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl \
  --candidates-a data/eval/runs/rtlcoder_synthetic_rule_baseline/candidates.jsonl \
  --name-a rule_baseline \
  --candidates-b data/eval/runs/qwen2_5_coder_7b_lora_pilot/candidates.jsonl \
  --name-b qwen2_5_coder_7b_lora_pilot \
  --output-md data/reports/finetune/qwen2_5_coder_7b_candidate_diff.md \
  --output-json data/reports/finetune/qwen2_5_coder_7b_candidate_diff.json \
  --json
```

## 8. Safety reminders

- Do not overwrite existing eval runs.
- Do not promote this dataset to golden.
- Do not mark rows reviewed or approved.
- Do not commit checkpoints, adapters, merged models, or weight files.
- Do not train directly on alias-carrying source splits when the canonical export step has not been run.
- Do not treat a strong score on this pilot as semantic proof of RTL quality.
