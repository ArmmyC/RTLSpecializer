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
Its `manifest.json` does not include a self SHA256 because embedding that hash would change the manifest bytes.

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

## 4. Check the live training runtime first

Use the repo check script inside the actual training runtime before starting LoRA:

```bash
python scripts/finetune/check_training_environment.py \
  --dataset-dir outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical \
  --expected-gpu-substring L40 \
  --json
```

Expected:

- all required packages import successfully
- `torch.cuda_available = true`
- at least one visible GPU name contains `L40`
- the dataset directory is visible from the current runtime

Important CPE note:

- the `gpul40` node may not be able to read `~/RTLSpecializer` directly
- use `scripts/finetune/stage_cpe_lora.sh` from the CPE login host; it streams the repo subset, canonical dataset, and Python site-packages into `/tmp` before starting any GPU-side command
- the launcher defaults to the environment check plus trainer dry-run. It only stages the local base-model artifact and starts training when `--train` is explicitly supplied.

From the CPE login host after extracting the transfer archive:

```bash
cd ~/RTLSpecializer
bash scripts/finetune/stage_cpe_lora.sh
```

To reuse an existing allocation, such as a controlled L40 reservation, pass its job ID:

```bash
bash scripts/finetune/stage_cpe_lora.sh --job-id <job-id>
```

When the dry-run has been reviewed and you are ready for the controlled training attempt:

```bash
bash scripts/finetune/stage_cpe_lora.sh --smoke-train
```

`--smoke-train` loads the actual local Qwen model, attaches LoRA, builds TRL's `SFTTrainer`, performs one optimizer step, saves its artifacts, and restores them under `outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora_smoke`. It is the required compatibility check before a full run.

For the full run only after the smoke artifacts and logs are reviewed:

```bash
bash scripts/finetune/stage_cpe_lora.sh --train
```

`--train --max-steps N` runs an explicitly limited number of real optimization steps, but writes into the full-run adapter path. Prefer `--smoke-train` for the one-step check so the full output remains empty. Both training modes refuse to overwrite an existing persistent adapter output. The launcher stages the local Qwen model cache from `~/LLMModel/qwen25-coder-7b-instruct/models/Qwen__Qwen2.5-Coder-7B-Instruct` by default. Override that source with `--model-source-dir PATH` if needed.

## 5. Option A: Axolotl-style LoRA command template

This is a template only. Exact keys can vary slightly by Axolotl version.

```bash
accelerate launch -m axolotl.cli.train \
  configs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora.yaml
```

Adjust batch size, accumulation, and data-format wiring to match the installed Axolotl release.

## 6. Option B: concrete TRL / SFTTrainer repo script

Dry-run first:

```bash
python scripts/finetune/train_qwen2_5_coder_7b_lora.py \
  --dataset-dir outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical \
  --output-dir outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora \
  --expected-gpu-substring L40 \
  --dry-run \
  --json
```

When you are ready to start the real run later, drop `--dry-run`:

```bash
python scripts/finetune/train_qwen2_5_coder_7b_lora.py \
  --dataset-dir outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical \
  --output-dir outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora \
  --expected-gpu-substring L40 \
  --json
```

To limit a direct trainer invocation to one real update step, add `--max-steps 1`. Unlike `--dry-run`, this loads the model and executes the full training path.

The repo script does these preflight checks before model load:

- the dataset passes `scripts/finetune/check_finetune_dataset.py`
- schema aliases are zero, so the input is truly canonical
- the output directory is empty unless `--overwrite-output-dir` or `--resume-from-checkpoint` is used
- the runtime sees CUDA and a GPU whose name contains `L40`

The trainer path renders each structured user/assistant message object into canonical JSON text before applying the tokenizer chat template. It then removes the original `messages` column and supplies TRL a plain `text` dataset, preventing TRL from treating structured message content as strings itself.

## 7. QLoRA fallback only

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

## 8. Evaluate before and after

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

## 9. Safety reminders

- Do not overwrite existing eval runs.
- Do not promote this dataset to golden.
- Do not mark rows reviewed or approved.
- Do not commit checkpoints, adapters, merged models, or weight files.
- Do not train directly on alias-carrying source splits when the canonical export step has not been run.
- Do not treat a strong score on this pilot as semantic proof of RTL quality.
