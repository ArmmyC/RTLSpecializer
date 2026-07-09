# RTL teacher-distill pilot

This workflow adds a local baseline-vs-fine-tuned evaluation loop for the teacher-distilled RTL pilot dataset.

Important status:

- The dataset is teacher-distilled.
- It is unreviewed.
- It is not golden.
- It is not human-approved.
- It is pilot-quality only.

This workflow is mainly for formatting, structured-output, and claim-safety evaluation. It does not prove RTL correctness.

## Step 1: Prepare the distill dataset

If the dataset is not already present, prepare it first:

```bash
python scripts/dataset/prepare_teacher_distill_dataset.py \
  --tasks data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/review/verilog_eval_rtl_answer_v0_1_156_clean.jsonl \
  --output-dir data/distill/verilog_eval_teacher_distill_v0_1 \
  --train-size 120 \
  --validation-size 18 \
  --test-size 18 \
  --seed 42 \
  --strict \
  --json
```

## Step 2: Export a canonical training copy

Before any fine-tuning run, export a canonical copy for the trainer:

```bash
python scripts/finetune/export_canonical_finetune_dataset.py \
  --dataset-dir data/distill/verilog_eval_teacher_distill_v0_1 \
  --output-dir outputs/finetune_datasets/verilog_eval_teacher_distill_v0_1_canonical \
  --json
```

This keeps the source teacher-distill package unchanged while ensuring the actual training rows use canonical schema names:

- `messages[1].content.schema_version = rtl_task_v0.1`
- `messages[2].content.schema_version = rtl_answer_v0.1`

## Step 3: Export test prompts

Export the held-out prompts without leaking the expected answer into the prompt payload:

```bash
python scripts/eval/export_rtl_eval_prompts.py \
  --input data/distill/verilog_eval_teacher_distill_v0_1/test.jsonl \
  --output data/eval/rtl_teacher_distill_pilot/test_prompts.jsonl \
  --split test \
  --strict \
  --json
```

The exported JSONL keeps:

- exact system prompt,
- exact `rtl_task_v0.1` user content,
- expected `rtl_answer_v0.1` in a separate scoring field only,
- prompt metadata needed for later scoring.

## Step 4: Run a baseline model manually

Use any local/manual generation path you prefer:

- local OpenAI-compatible endpoint,
- llama.cpp server,
- vLLM,
- Ollama or OpenWebUI-compatible model,
- manual copy/paste and saved JSONL.

Save predictions as JSONL:

```json
{"source_id":"Prob062_bugs_mux2","model":"baseline_model_name","output":"{...json...}"}
```

`output` may be either:

- a parsed JSON object, or
- a raw string containing one `rtl_answer_v0.1` JSON object.

## Step 5: Score the baseline

```bash
python scripts/eval/score_rtl_answer_json.py \
  --prompts data/eval/rtl_teacher_distill_pilot/test_prompts.jsonl \
  --predictions data/eval/rtl_teacher_distill_pilot/baseline_predictions.jsonl \
  --output-json data/eval/rtl_teacher_distill_pilot/baseline_scores.json \
  --output-md data/eval/rtl_teacher_distill_pilot/baseline_scores.md \
  --strict \
  --json
```

The scorer emphasizes:

- JSON validity,
- schema validity,
- source ID matching,
- conservative claim safety,
- reference-only behavior,
- prompt-embedded candidate-bug behavior.

Exact match to the teacher answer is report-only and not the main score.

## Step 6: Run a small LoRA/QLoRA pilot manually

Start from the template:

```text
configs/finetune/rtl_teacher_distill_pilot_lora.yaml
```

The template is framework-agnostic. Adapt it to your trainer of choice and keep the run small:

- 1 to 3 epochs,
- low learning rate,
- small batch size,
- train on the canonical export rather than the raw source distill package,
- conservative expectations.

## Step 7: Run the fine-tuned model on the same test prompts

Use the exact same `test_prompts.jsonl` file and save predictions to a second JSONL file:

```text
data/eval/rtl_teacher_distill_pilot/finetuned_predictions.jsonl
```

## Step 8: Score the fine-tuned model

```bash
python scripts/eval/score_rtl_answer_json.py \
  --prompts data/eval/rtl_teacher_distill_pilot/test_prompts.jsonl \
  --predictions data/eval/rtl_teacher_distill_pilot/finetuned_predictions.jsonl \
  --output-json data/eval/rtl_teacher_distill_pilot/finetuned_scores.json \
  --output-md data/eval/rtl_teacher_distill_pilot/finetuned_scores.md \
  --strict \
  --json
```

## Step 9: Compare baseline vs fine-tuned

```bash
python scripts/eval/compare_rtl_eval_runs.py \
  --baseline data/eval/rtl_teacher_distill_pilot/baseline_scores.json \
  --finetuned data/eval/rtl_teacher_distill_pilot/finetuned_scores.json \
  --output-md data/eval/rtl_teacher_distill_pilot/comparison.md \
  --output-json data/eval/rtl_teacher_distill_pilot/comparison.json \
  --json
```

The comparison highlights:

- `overall_valid` rate,
- JSON/schema/claim-safety rates,
- reference-only behavior rate,
- candidate-bug behavior rate,
- source-level improvements,
- source-level regressions.

## Step 10: Decide what to do next

Use the comparison to decide whether to:

- collect more bug-focused rows,
- adjust prompts or fine-tune settings,
- tighten post-processing,
- or stop because the pilot is overfitting or regressing claim safety.

## Main limitations

- This pilot measures formatting and conservative answer behavior more than deep RTL quality.
- It does not prove semantic correctness.
- The dataset is not golden.
- The dataset is not human-reviewed.
- More prompt-embedded bug rows and more reviewed bug-focused data are needed before making serious model-quality claims.
