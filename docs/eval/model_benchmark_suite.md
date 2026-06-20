# Local model benchmark suite

The benchmark suite repeats candidate generation and deterministic evaluation across local model configurations:

```text
release/test.jsonl -> benchmark suite -> per-model candidates/evaluation -> benchmark summary
```

It orchestrates existing local tools. It does not train or download models, start servers, execute RTL or testbenches, run EDA tools, or treat evaluator scores as proof of correctness.

## JSON configuration

Only JSON is supported. Model and run names must be unique filesystem-safe identifiers.

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
      "name": "local_model_a",
      "model": "Local-Model-A",
      "endpoint": "http://127.0.0.1:8000/v1/chat/completions"
    },
    {
      "name": "local_model_b",
      "model": "Local-Model-B",
      "endpoint": "http://127.0.0.1:8001/v1/chat/completions"
    }
  ]
}
```

Recommended normal local settings are `temperature: 0.0`, `max_tokens: 8192`, and `timeout: 120`. A large maximum token cap does not force a long answer, but it gives a poorly controlled model room to ramble; inspect parse failures and raw output before increasing it further.

`include_rule_baseline` adds the repository’s conservative offline baseline to the same table. It makes no endpoint request and provides a stable comparison point.

## Network safety

Endpoints are localhost-only by default. A non-local endpoint runs only when both conditions are true:

- the individual model object sets `"allow_nonlocal_endpoint": true`;
- the CLI includes `--allow-nonlocal-endpoint`.

This double opt-in does not make private RTL safe to upload. `api_key_env` may name an environment variable, but its value is never written to resolved configs, candidates, summaries, or logs. Unsupported config keys are rejected so secret-bearing fields are not copied into reports.

Localhost is only a network boundary: the local server process and its operator can read all submitted prompts and RTL. Review the server operator, configuration, and retention policy before inference. For any non-local endpoint, review the selected dataset again before granting both opt-ins.

## First run and dry-run

Start with the network-free fixture smoke test. All model jobs are forced into candidate-runner dry-run mode:

```bash
python scripts/eval/run_benchmark_suite.py \
  --config tests/fixtures/eval/benchmark_suite_dry_run.json \
  --output-dir /tmp/rtl_specializer_benchmark_suite_dry_run \
  --dry-run \
  --json \
  --overwrite
```

After reviewing the resolved configuration, run a small local benchmark:

```bash
python scripts/eval/run_benchmark_suite.py \
  --config configs/benchmarks/verilog_eval_local_models_v0.1.json \
  --output-dir data/eval/benchmarks/verilog_eval_local_models_v0.1 \
  --limit 3 \
  --json \
  --overwrite
```

Dry-run model placeholders are intentionally not valid answers, so their evaluator result is recorded as unsuccessful while the suite itself completes with a warning. The offline rule baseline still generates and evaluates normally.

`--limit` and repeated `--row-id` filters apply to every job. `--resume` continues existing model candidate files and creates missing jobs. `--overwrite` replaces only exact known suite, candidate, report, and evaluator files. Unknown files are preserved. The flags are mutually exclusive.

Use `--evaluate-only` to evaluate existing configured candidate files without model calls. Use `--skip-candidates` to avoid both generation and evaluation and aggregate existing candidate reports and evaluator metrics. These modes are mutually exclusive.

## Outputs

The suite writes these exact files under `--output-dir`:

- `benchmark_config.resolved.json`
- `benchmark_summary.json`
- `benchmark_summary.md`
- `benchmark_summary.csv`
- `models/<name>/candidate_report.json`
- `models/<name>/evaluation_metrics.json`
- `models/<name>/links.json`

Candidate and evaluator outputs use `candidate_dir` and `eval_dir`. When omitted, non-overlapping sibling directories are derived from `--output-dir`. Output roots, job directories, and raw-output directories must not overlap dangerously, be symlinks or traverse symlinked directory ancestry, contain the dataset, target filesystem/repository/home roots, or reside inside `.local_data`.

All benchmark summaries, candidates, raw responses, and evaluator artifacts are generated local outputs. Keep them out of commits unless they have been deliberately reviewed and approved.

The Markdown comparison table sorts by mean score descending, safety failures ascending, then model name. CSV contains scalar fields for downstream analysis.

## Limitations

The deterministic evaluator checks structure, grounding signals, conservative claims, and fixed heuristics. Scores are descriptive, are not statistical significance claims, and do not prove functional correctness, equivalence, timing, area, activity, or power.
