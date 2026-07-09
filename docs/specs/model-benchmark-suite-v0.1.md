# Feature Spec: Model Benchmark Suite v0.1

## 1. Goal

Add a local benchmark-suite runner that evaluates one or more model candidate-generation configurations against a dataset split and produces a comparable benchmark report.

The repository now has:

```text
release/test.jsonl -> model candidate runner -> candidate JSONL -> deterministic evaluator
```

The next highest-value step is a small orchestration layer so experiments are repeatable across multiple local models, max-token settings, prompt templates, and endpoints.

The benchmark suite should:

```text
benchmark_config.json
  -> run rule baseline optionally
  -> run local/user-configured model candidate jobs
  -> evaluate each candidate file
  -> aggregate metrics
  -> write benchmark_summary.json/md/csv
```

This is a local evaluation orchestration tool. It must not train models, download models, execute RTL, run EDA tools, or call non-local endpoints unless explicitly configured through the same safety mechanism as the candidate runner.

## 2. Non-goals

Do not build:

- model training,
- fine-tuning,
- LoRA/DoRA adapters,
- model downloading,
- automatic model server startup,
- web dashboard,
- database storage,
- cloud endpoint defaults,
- prompt optimization loops,
- statistical significance claims beyond simple descriptive reporting,
- RTL simulation/synthesis/equivalence/toggle/power analysis,
- schema changes.

Do not commit generated benchmark artifacts by default.

## 3. User stories

- As a researcher, I want to compare several local models on the same dataset split with consistent settings.
- As a dataset maintainer, I want benchmark outputs grouped by run ID so I can reproduce results later.
- As a model evaluator, I want one summary table with mean score, matched rows, parse failures, validation failures, safety failures, and latency if available.
- As a local-model user, I want to run dry-run and small `--limit` smoke tests before a full benchmark.
- As a project lead, I want benchmark metadata to record model name, endpoint host, prompt template, temperature, max tokens, and candidate/eval output paths.

## 4. CLI UX

Add:

```text
scripts/eval/run_benchmark_suite.py
```

Example:

```bash
python scripts/eval/run_benchmark_suite.py \
  --config configs/benchmarks/verilog_eval_local_models_v0.1.json \
  --output-dir data/eval/benchmarks/verilog_eval_local_models_v0.1 \
  --json
```

Supported options:

```text
--config <path>              benchmark suite JSON config
--output-dir <path>          benchmark output directory
--run-id <name>              optional override for report run_id
--limit <int>                optional row limit applied to every model job
--row-id <id>                optional repeatable row filter applied to every model job
--dry-run                    force dry-run for every model job
--resume                     resume candidate generation where possible
--overwrite                  replace exact generated suite outputs
--skip-candidates            only aggregate existing candidate/eval outputs
--evaluate-only              skip candidate generation, run evaluator on existing candidates
--allow-nonlocal-endpoint    allow non-local endpoints for model jobs that request them
--json                       print JSON summary
```

`--resume` and `--overwrite` are mutually exclusive.

## 5. Config format

Use JSON only, no YAML dependency.

Example:

```json
{
  "run_id": "verilog_eval_local_models_v0.1",
  "dataset": "data/releases/release_v0.1_plus_verilog_eval_001/test.jsonl",
  "include_rule_baseline": true,
  "candidate_dir": "data/eval/candidates",
  "eval_dir": "data/eval/runs",
  "defaults": {
    "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
    "prompt_template": "rtl_answer_v0.1_default",
    "temperature": 0.0,
    "max_tokens": 8192,
    "timeout": 120,
    "retries": 1,
    "strict": false
  },
  "models": [
    {
      "name": "qwen3_coder_30b_a3b",
      "model": "Qwen3-Coder-30B-A3B-Instruct",
      "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
      "max_tokens": 8192
    },
    {
      "name": "qwen2_5_coder_32b",
      "model": "Qwen2.5-Coder-32B-Instruct",
      "endpoint": "http://127.0.0.1:8001/v1/chat/completions",
      "max_tokens": 8192
    }
  ]
}
```

Required config fields:

```text
run_id
dataset
models
```

Optional config fields:

```text
include_rule_baseline
candidate_dir
eval_dir
defaults
models[].endpoint
models[].api_key_env
models[].prompt_template
models[].temperature
models[].top_p
models[].max_tokens
models[].timeout
models[].retries
models[].strict
models[].raw_output_dir
models[].allow_nonlocal_endpoint
```

Each `models[].name` must be filesystem-safe and unique.

## 6. Output layout

Given:

```text
--output-dir data/eval/benchmarks/verilog_eval_local_models_v0.1
```

Write:

```text
data/eval/benchmarks/verilog_eval_local_models_v0.1/
  benchmark_config.resolved.json
  benchmark_summary.json
  benchmark_summary.md
  benchmark_summary.csv
  models/
    qwen3_coder_30b_a3b/
      candidate_report.json
      evaluation_metrics.json
      links.json
    qwen2_5_coder_32b/
      candidate_report.json
      evaluation_metrics.json
      links.json
```

Candidate files should be written under configured `candidate_dir`, for example:

```text
data/eval/candidates/<run_id>__<model_name>.jsonl
```

Evaluation runs should be written under configured `eval_dir`, for example:

```text
data/eval/runs/<run_id>__<model_name>/
```

All generated paths are local-only by existing `.gitignore` policy unless the user intentionally publishes them later.

## 7. Functional requirements

### FR-1: Config validation

Validate:

- config file is JSON object,
- `dataset` exists and validates through evaluator loader,
- model names are unique and filesystem-safe,
- no output path is inside `.local_data`,
- candidate/eval/output directories do not overlap dangerously,
- `--resume` and `--overwrite` are mutually exclusive,
- non-local endpoints are rejected unless both the model config and CLI allow them.

### FR-2: Rule baseline option

If `include_rule_baseline` is true, generate conservative rule baseline candidates using existing `make_candidates`, evaluate them, and include them in the benchmark summary as model name:

```text
rule_baseline
```

The rule baseline should not call any model endpoint.

### FR-3: Model candidate jobs

For every model config, call existing `run_model_candidates` module logic, not a subprocess.

Pass through:

- dataset,
- output,
- model,
- endpoint,
- api_key_env,
- prompt_template,
- temperature,
- top_p,
- max_tokens,
- timeout,
- retries,
- limit,
- row IDs,
- resume/overwrite/dry-run,
- raw output dir,
- strict,
- allow_nonlocal_endpoint.

If `--dry-run` is supplied to the suite, force every model job to dry-run regardless of config.

### FR-4: Evaluation

After each candidate job succeeds, evaluate candidate answers using existing deterministic evaluator unless the candidate job already did it and paths are available.

For `--evaluate-only`, skip candidate generation and evaluate existing candidate files.

For `--skip-candidates`, do not generate candidates or evaluate; only aggregate existing reports/metrics if present.

### FR-5: Aggregation

Aggregate per-model fields:

```json
{
  "name": "qwen3_coder_30b_a3b",
  "kind": "model",
  "candidate_ok": true,
  "evaluation_ok": true,
  "candidate_rows": 100,
  "matched_rows": 100,
  "mean_score": 0.72,
  "median_score": 0.70,
  "safety_failures": 0,
  "parse_failed": 0,
  "endpoint_failed": 0,
  "candidate_invalid": 2,
  "candidate_output": "...",
  "evaluation_output_dir": "...",
  "model": "Qwen3-Coder-30B-A3B-Instruct",
  "endpoint_host": "127.0.0.1",
  "prompt_template": "rtl_answer_v0.1_default",
  "temperature": 0.0,
  "max_tokens": 8192
}
```

Sort Markdown table by `mean_score` descending, then `safety_failures` ascending, then name.

### FR-6: Reports

Write:

```text
benchmark_summary.json
benchmark_summary.md
benchmark_summary.csv
```

Markdown should include:

- run settings,
- dataset path,
- row limit/filters,
- table of all models,
- failure section,
- limitations section,
- exact rerun command.

CSV should be simple comma-separated text with a header row and only scalar fields.

### FR-7: Output safety

- If output directory exists and is non-empty, require `--resume` or `--overwrite`.
- `--overwrite` may replace only exact generated suite files under `--output-dir` and exact candidate/evaluation paths for configured jobs.
- Do not delete unknown files unless they are inside exact generated model subdirectories for the same run ID and overwrite is explicit.
- Never write inside `.local_data`.
- Do not follow symlinks for directory cleanup.

### FR-8: Tests

Add tests under:

```text
tests/eval/test_benchmark_suite.py
```

Required tests:

- config validation rejects duplicate model names,
- config validation rejects unsafe model names,
- non-local endpoint rejected unless config and CLI allow it,
- dry-run suite creates summary without endpoint calls,
- include_rule_baseline generates and evaluates baseline,
- suite calls model runner for each model config using monkeypatch/fake runner,
- aggregation sorts by score and includes parse/validation counts,
- CSV summary is written and parseable,
- output dir protection fails without resume/overwrite,
- overwrite preserves unknown parent files and does not follow symlink dirs,
- evaluate-only uses existing candidate files,
- CLI JSON output is parseable.

No tests should call real network endpoints.

## 8. Docs

Create:

```text
docs/eval/model_benchmark_suite.md
```

Update:

```text
README.md
docs/eval/model_candidate_runner.md
docs/eval/evaluation_harness.md
```

Docs must explain:

```text
release/test.jsonl -> benchmark suite -> per-model candidates/eval -> benchmark summary
```

Include:

- sample JSON config,
- dry-run smoke command,
- two-local-model example,
- recommended first run with `--limit 3`,
- recommended normal local settings: `temperature=0.0`, `max_tokens=8192`, `timeout=120`,
- warning that large max-token caps do not force long output but may allow rambling,
- how to compare against rule baseline,
- limitations: deterministic evaluator is a heuristic, not proof of correctness.

## 9. Security and safety

Treat dataset rows, model configs, and model outputs as untrusted text.

Do not execute RTL, testbenches, generated text, or shell commands from config.

Do not call non-local endpoints unless explicitly allowed.

Do not log API keys.

Do not store API keys in config resolution, reports, candidates, or logs.

Do not train, fine-tune, or download models.

## 10. Files likely involved

Create:

```text
scripts/eval/benchmark_suite.py
scripts/eval/run_benchmark_suite.py
tests/eval/test_benchmark_suite.py
docs/eval/model_benchmark_suite.md
```

Modify:

```text
README.md
docs/eval/model_candidate_runner.md
docs/eval/evaluation_harness.md
```

Do not modify dataset schemas.

## 11. Testing plan

Run:

```bash
python -m pytest tests/eval/test_benchmark_suite.py
python -m pytest tests/eval tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Run dry-run smoke:

```bash
python scripts/eval/run_benchmark_suite.py \
  --config tests/fixtures/eval/benchmark_suite_dry_run.json \
  --output-dir /tmp/rtl_specializer_benchmark_suite_dry_run \
  --dry-run \
  --json \
  --overwrite
```

If the user has local model endpoints running, optionally run a real small benchmark:

```bash
python scripts/eval/run_benchmark_suite.py \
  --config configs/benchmarks/verilog_eval_local_models_v0.1.json \
  --output-dir data/eval/benchmarks/verilog_eval_local_models_v0.1 \
  --limit 3 \
  --json \
  --overwrite
```

Do not fail CI if no local endpoint is running.

## 12. Definition of done

Done only when:

- Suite CLI exists.
- Suite config JSON format is documented and validated.
- Rule baseline can be included.
- Multiple model jobs can be run or dry-run.
- Candidate runner and evaluator are reused instead of duplicated.
- JSON/Markdown/CSV summaries are written.
- Output safety covers overwrite/resume/symlink/.local_data cases.
- Tests cover config validation, endpoint safety, dry-run, aggregation, output safety, and CLI JSON.
- Docs explain how to run a 3-row smoke benchmark and a full local benchmark.
- No training, downloads, EDA execution, RTL execution, schema changes, or secret logging are introduced.

## 13. Codex implementation instructions

Implement this spec exactly.

This feature orchestrates local benchmark runs. It must not train, fine-tune, download models, run EDA tools, execute RTL, or call non-local endpoints unless explicitly allowed.

After finishing, commit and push. Summarize changed files, commands run, test results, smoke results, and tradeoffs.
