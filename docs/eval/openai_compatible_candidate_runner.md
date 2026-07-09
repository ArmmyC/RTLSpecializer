# OpenAI-compatible candidate runner

`run_openai_compatible_candidates.py` calls a user-supplied OpenAI-compatible `/v1/chat/completions` service and writes evaluator-ready candidate JSONL:

```text
dataset split -> hosted model candidates -> evaluate_answers.py
```

It does inference only. It does not train or fine-tune a model, execute RTL or testbenches, run EDA tools, or promote any data.

## Prompt contract

- Only `messages[0]` and `messages[1]` from each dataset row are sent to the model.
- `messages[2]`, the reference assistant answer, is never sent.
- Non-string user content is serialized to JSON text before the request.

## Run a smoke test

```bash
python scripts/eval/run_openai_compatible_candidates.py \
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl \
  --output data/eval/runs/rtlcoder_synthetic_active_model_smoke/candidates.jsonl \
  --raw-output-dir data/eval/runs/rtlcoder_synthetic_active_model_smoke/raw \
  --base-url http://127.0.0.1:8000/v1 \
  --model active-model \
  --temperature 0 \
  --max-tokens 2048 \
  --limit 3 \
  --json
```

The API key is read from `RTLSPEC_EVAL_API_KEY` by default, or from the env var named by `--api-key-env`. The key is never printed or written to candidate metadata, raw outputs, or logs.

## Output behavior

Each row produces one candidate entry:

```json
{
  "id": "row_id",
  "answer": {"schema_version": "rtl_answer_v0.1"},
  "metadata": {
    "model": "active-model",
    "base_url": "http://127.0.0.1:8000/v1",
    "temperature": 0,
    "max_tokens": 2048,
    "raw_output_path": null,
    "parse_error": null
  }
}
```

- Direct JSON, fenced JSON, and JSON surrounded by extra text are parsed conservatively.
- Parse failures are preserved as `{"schema_version":"parse_error", ...}` rows.
- API failures after retries are preserved as `{"schema_version":"api_error", ...}` rows unless `--fail-fast` stops the run after writing the failed row.
- `--resume` keeps existing candidate rows unchanged and appends only missing IDs.
- `--raw-output-dir` stores raw model text only, under safe row-ID-based filenames.

## Evaluate candidates

```bash
python scripts/eval/evaluate_answers.py \
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl \
  --candidates data/eval/runs/rtlcoder_synthetic_active_model_smoke/candidates.jsonl \
  --output-dir data/eval/runs/rtlcoder_synthetic_active_model_smoke \
  --json
```

The deterministic evaluator scores structure, grounding, and conservative claim behavior. It is not proof of RTL correctness.
