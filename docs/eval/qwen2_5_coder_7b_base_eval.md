# Qwen2.5-Coder-7B Base Evaluation

Run this base-model evaluation before any LoRA training for the RTLCoder synthetic teacher-distill pilot.

Target model:

- `Qwen/Qwen2.5-Coder-7B-Instruct`

Held-out dataset:

- `data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl`

Canonical fine-tune dataset path:

- `outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical`

This step does not train a model. It measures the exact small target base model against the existing evaluation harness before any adapter tuning.

## Flow

1. Serve `Qwen/Qwen2.5-Coder-7B-Instruct` behind the OpenAI-compatible `/v1` API.
2. Confirm `/v1/models` reports the expected model identity.
3. Run schema-forced candidate generation on the 100-row held-out test split with:
   - `scripts/eval/run_openai_compatible_candidates.py`
   - `docs/eval/rtl_answer_schema_reminder.md`
   - `--response-format-json`
   - `--resume`
   - raw output capture
4. Evaluate the generated candidates with `scripts/eval/evaluate_answers.py`.
5. Compare the resulting run against:
   - `data/eval/runs/rtlcoder_synthetic_rule_baseline`
   - `data/eval/runs/rtlcoder_synthetic_active_model_base_schema`
6. Inspect candidate differences against:
   - the rule baseline
   - the hosted active-model baseline if its candidate file is available

## Endpoint check

Use the local evaluation API key from `RTLSPEC_EVAL_API_KEY` and confirm the served model before generating any candidates:

```powershell
curl.exe http://100.114.11.91:8000/v1/models `
  -H "Authorization: Bearer $env:RTLSPEC_EVAL_API_KEY"
```

If the server exposes an alias such as `active-model`, confirm that alias is actually backed by `Qwen/Qwen2.5-Coder-7B-Instruct` before proceeding.

## Recommended command template

Use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/eval/run_qwen2_5_coder_7b_base_eval.ps1
```

That template:

- fails early if `RTLSPEC_EVAL_API_KEY` is missing,
- reminds you to verify `/v1/models`,
- uses the schema reminder file,
- preserves raw responses,
- avoids overwriting an existing eval run directory,
- runs comparison and difference reports after evaluation.

## Direct commands

Candidate generation:

```powershell
python scripts/eval/run_openai_compatible_candidates.py `
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl `
  --output data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl `
  --base-url http://100.114.11.91:8000/v1 `
  --model active-model `
  --api-key-env RTLSPEC_EVAL_API_KEY `
  --temperature 0 `
  --max-tokens 2048 `
  --timeout 120 `
  --resume `
  --raw-output-dir data/eval/raw_outputs/qwen2_5_coder_7b_base_schema `
  --schema-reminder-file docs/eval/rtl_answer_schema_reminder.md `
  --response-format-json `
  --json
```

Evaluation:

```powershell
python scripts/eval/evaluate_answers.py `
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl `
  --candidates data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl `
  --output-dir data/eval/runs/qwen2_5_coder_7b_base_schema `
  --json
```

Comparison:

```powershell
python scripts/eval/compare_eval_runs.py `
  --runs `
    data/eval/runs/rtlcoder_synthetic_rule_baseline `
    data/eval/runs/rtlcoder_synthetic_active_model_base_schema `
    data/eval/runs/qwen2_5_coder_7b_base_schema `
  --output-md data/reports/eval/qwen2_5_coder_7b_base_schema_comparison.md `
  --output-json data/reports/eval/qwen2_5_coder_7b_base_schema_comparison.json `
  --json
```

Rule-baseline candidate difference inspection:

```powershell
python scripts/eval/inspect_candidate_differences.py `
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl `
  --candidates-a data/eval/runs/rtlcoder_synthetic_rule_baseline/candidates.jsonl `
  --name-a rule_baseline `
  --candidates-b data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl `
  --name-b qwen2_5_coder_7b_base_schema `
  --output-md data/reports/eval/qwen2_5_coder_7b_base_schema_vs_rule_diff.md `
  --output-json data/reports/eval/qwen2_5_coder_7b_base_schema_vs_rule_diff.json `
  --json
```

Optional hosted active-model candidate difference inspection:

```powershell
python scripts/eval/inspect_candidate_differences.py `
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl `
  --candidates-a data/eval/runs/rtlcoder_synthetic_active_model_base_schema/candidates.jsonl `
  --name-a hosted_active_model_base_schema `
  --candidates-b data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl `
  --name-b qwen2_5_coder_7b_base_schema `
  --output-md data/reports/eval/qwen2_5_coder_7b_base_schema_vs_active_diff.md `
  --output-json data/reports/eval/qwen2_5_coder_7b_base_schema_vs_active_diff.json `
  --json
```

## Expected artifacts

Local generated artifacts should remain uncommitted:

- `data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl`
- `data/eval/raw_outputs/qwen2_5_coder_7b_base_schema/`
- `data/eval/runs/qwen2_5_coder_7b_base_schema/`
- `data/reports/eval/qwen2_5_coder_7b_base_schema_comparison.md`
- `data/reports/eval/qwen2_5_coder_7b_base_schema_comparison.json`
- `data/reports/eval/qwen2_5_coder_7b_base_schema_vs_rule_diff.md`
- `data/reports/eval/qwen2_5_coder_7b_base_schema_vs_rule_diff.json`

Do not start LoRA training until this base-model evaluation has been completed and reviewed.
