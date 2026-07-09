# Teacher-distill fine-tune pilot workflow

This workflow packages clean `rtl_task_v0.1` rows plus clean teacher `rtl_answer_v0.1` rows into a local pilot fine-tuning dataset.

It is intentionally conservative:

- It is teacher-distilled.
- It is not human-reviewed.
- It is not golden.
- It is for format/pipeline/pilot fine-tuning only.
- It should not be used as final production truth without review.

The workflow does not call external APIs, does not train a model itself, does not execute RTL/testbenches, and does not run EDA tools.

RTLCoder-derived tasks can enter this flow only after the raw index has been normalized into reference `rtl_task_v0.1` rows, optionally expanded into synthetic buggy-candidate tasks, and then paired with validated teacher answers. See `docs/dataset/rtlcoder_synthetic_bug_workflow.md`.

## Flow

```text
clean rtl_task_v0.1 JSONL
  -> clean teacher rtl_answer_v0.1 JSONL
  -> prepare teacher-distill dataset
  -> deterministic train/validation/test split
  -> export canonical fine-tune copy
  -> run baseline eval first
  -> run small LoRA/QLoRA pilot
  -> compare baseline vs fine-tuned model
  -> collect more bug-focused data later
```

## Inputs

Expected clean inputs:

```text
data/normalized/tasks/verilog_eval_rtl_task_v0_1_156.jsonl
data/answers/assembled/verilog_eval_rtl_answer_v0_1_156_clean.jsonl
```

The task file should contain clean `rtl_task_v0.1` rows.
The answer file should contain clean teacher `rtl_answer_v0.1` rows.

For RTLCoder-derived inputs, "clean" still does not mean "verified." It means:

- provenance remains `external_rtlcoder_gpt_generated_unverified`,
- licensing is still unconfirmed until manual review,
- synthetic buggy candidates were created by deterministic text mutation only,
- no row has been promoted to golden.

## Step 1: Prepare the pilot dataset

```bash
python scripts/dataset/prepare_teacher_distill_dataset.py \
  --tasks data/normalized/tasks/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/answers/assembled/verilog_eval_rtl_answer_v0_1_156_clean.jsonl \
  --output-dir data/distill/verilog_eval_teacher_distill_v0_1 \
  --train-size 0.77 \
  --val-size 0.115 \
  --test-size 0.115 \
  --seed 42 \
  --strict \
  --json
```

This writes:

```text
data/distill/verilog_eval_teacher_distill_v0_1/all.jsonl
data/distill/verilog_eval_teacher_distill_v0_1/train.jsonl
data/distill/verilog_eval_teacher_distill_v0_1/validation.jsonl
data/distill/verilog_eval_teacher_distill_v0_1/test.jsonl
data/distill/verilog_eval_teacher_distill_v0_1/manifest.json
data/distill/verilog_eval_teacher_distill_v0_1/dataset_card.md
data/distill/verilog_eval_teacher_distill_v0_1/validation_report.json
data/distill/verilog_eval_teacher_distill_v0_1/validation_report.md
```

Each chat row keeps:

- `messages[0]`: RTL review specialist instruction
- `messages[1]`: the original `rtl_task_v0.1` JSON
- `messages[2]`: the matching `rtl_answer_v0.1` JSON

The tool does not rewrite task or answer content.

## Step 2: Validate the merged pilot rows

```bash
python scripts/dataset/validate_dataset.py \
  --input data/distill/verilog_eval_teacher_distill_v0_1/all.jsonl \
  --strict
```

The resulting rows remain explicitly:

- `review_status: teacher_distilled_unreviewed`
- `approval_status: not_approved`
- `promotion_allowed: false`

## Step 3: Export a canonical training copy

Before training, export a canonical copy for the trainer so the actual training rows use canonical schema names even if the source teacher-distill package still contains accepted aliases:

```bash
python scripts/finetune/export_canonical_finetune_dataset.py \
  --dataset-dir data/distill/verilog_eval_teacher_distill_v0_1 \
  --output-dir outputs/finetune_datasets/verilog_eval_teacher_distill_v0_1_canonical \
  --json
```

That export rewrites only:

- `messages[1].content.schema_version -> rtl_task_v0.1`
- `messages[2].content.schema_version -> rtl_answer_v0.1`

It does not modify the original `data/distill/...` package.

## Step 4: Run baseline evaluation first

Before any fine-tuning pilot, run the deterministic baseline evaluation flow on the held-out split you plan to compare against later.

The point of this step is not to prove correctness. It is to establish a reproducible baseline before changing model weights.

## Step 5: Run a small LoRA/QLoRA pilot

Use the teacher-distill dataset only for a small pilot:

- verify chat formatting and loader behavior,
- confirm the train/validation/test packaging works end to end,
- train on the canonical export rather than the raw alias-carrying source splits,
- measure whether the fine-tuned model improves over the baseline on the held-out split,
- avoid treating the teacher answers as reviewed truth.

## Step 6: Compare baseline vs fine-tuned model

Compare:

- baseline model outputs,
- fine-tuned pilot outputs,
- deterministic evaluation summaries,
- failure modes on the small number of prompt-embedded candidate bug rows.

Treat any apparent gains as provisional until reviewed data is available.

## Step 7: Collect more bug-focused data later

The main limitation of this pilot dataset is that most rows are reference-only, with only a small number of prompt-embedded candidate bug rows.

The RTLCoder synthetic-bug path is one way to increase candidate-bug coverage, but those rows are still draft inputs. They should not be treated as proven bug labels or as reviewed truth.

After the pilot:

- collect more bug-focused rows,
- review high-value answers manually,
- confirm provenance/license before broader release,
- only then consider promotion or a larger training run.
