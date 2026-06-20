# Local model candidate runner

The model candidate runner connects a validated dataset split to an OpenAI-compatible chat endpoint and writes candidate JSONL for the existing deterministic evaluator:

```text
release/test.jsonl -> model candidate runner -> candidate JSONL -> evaluator
```

It performs inference only. It does not train or download models, execute RTL or testbenches, run EDA tools, or treat model claims as verified evidence.

## Dry-run smoke

Dry-run builds prompts and writes three evaluator-shaped placeholder candidates plus JSON/Markdown reports. It makes no network request.

```bash
python scripts/eval/run_model_candidates.py \
  --dataset data/golden/golden_v0.1.jsonl \
  --output /tmp/rtl_specializer_model_candidates_dry_run.jsonl \
  --model dry-run-model \
  --limit 3 \
  --dry-run \
  --json \
  --overwrite
```

Dry-run rows use `parse_status: dry_run`, `validation_status: not_validated`, and `attempts: 0`.

## Run a local endpoint

The default endpoint is `http://127.0.0.1:8000/v1/chat/completions`. Only `127.0.0.1`, `localhost`, and `::1` are accepted by default.

```bash
python scripts/eval/run_model_candidates.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --output data/eval/candidates/local_model_v0.1.jsonl \
  --model local-model-name \
  --temperature 0.0 \
  --max-tokens 2048 \
  --timeout 120 \
  --json
```

A non-local endpoint is rejected unless `--allow-nonlocal-endpoint` is explicitly supplied. This flag authorizes the configured endpoint; it does not make uploaded RTL safe or public. Review dataset contents before sending them outside the machine. If authentication is required, pass an environment variable name with `--api-key-env`; the key is read at runtime and is never stored in candidate metadata or reports.

## Prompt and output contract

The `rtl_answer_v0.1_default` prompt includes the task, user goal, extracted summary, artifacts, constraints, and tool-check status. It excludes the dataset reference answer. The model is instructed to return one `rtl_answer_v0.1` object, use conservative claim levels, and include lint/compile and focused simulation in its verification plan.

Each candidate line contains:

```json
{
  "id": "row_id",
  "answer": {"schema_version": "rtl_answer_v0.1"},
  "metadata": {
    "model": "local-model-name",
    "runner": "run_model_candidates.py",
    "prompt_template": "rtl_answer_v0.1_default",
    "prompt_version": "v0.1",
    "endpoint_host": "127.0.0.1",
    "temperature": 0.0,
    "top_p": null,
    "max_tokens": 2048,
    "attempts": 1,
    "parse_status": "parsed_json",
    "validation_status": "candidate_valid",
    "raw_output_path": null
  }
}
```

Direct JSON is preferred. The runner can conservatively extract the first valid object from surrounding text. Arrays, scalars, full candidate rows, and unparseable output are rejected. Failed rows remain represented by an object-shaped placeholder and explicit failure metadata; model output is never executed.

## Output safety and resuming

- An existing output fails unless `--resume` or `--overwrite` is supplied; the flags are mutually exclusive.
- `--resume` validates unique existing IDs, preserves those rows, and generates only missing selected IDs.
- `--overwrite` replaces the exact candidate file and its two sidecar reports.
- Candidate, raw, and evaluation outputs inside `.local_data` are rejected.
- `--raw-output-dir` optionally stores exact model text under sanitized, hash-suffixed filenames. Raw output may contain sensitive RTL or model text and should remain local.
- `--strict` returns failure when any generated row has parse or validation errors. Endpoint failures are fatal in all modes.

Every completed run writes `<output-stem>.report.json` and `<output-stem>.report.md` with settings, counts, failures, and an evaluation command.

## Evaluate candidates

Generate and evaluate in one command with `--evaluate-output-dir`, or run the evaluator separately:

```bash
python scripts/eval/evaluate_answers.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --candidates data/eval/candidates/local_model_v0.1.jsonl \
  --output-dir data/eval/runs/local_model_v0.1 \
  --json
```

Evaluator scores are deterministic structural and evidence-safety heuristics. They are not semantic proof, simulation results, equivalence results, or proof of RTL correctness.

To compare multiple local model configurations on the same filtered rows, use the [local model benchmark suite](model_benchmark_suite.md). It reuses this runner’s endpoint, parsing, validation, resume, and output-safety behavior and adds rule-baseline comparison plus aggregate JSON, Markdown, and CSV reports.
