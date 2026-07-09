# Process Spec: First Local Benchmark Runbook v0.1

## 1. Goal

Add a practical runbook and example benchmark configuration so the next milestone is producing the first local benchmark result instead of adding more infrastructure.

The intended user workflow is:

```text
manual review -> readiness -> finalization -> dry-run benchmark -> small local benchmark -> inspect results
```

This is a docs/config task. Do not add new source-code features.

## 2. Files to add

Create:

```text
configs/benchmarks/README.md
configs/benchmarks/verilog_eval_local_models.example.json
docs/eval/first_local_benchmark_runbook.md
```

Update:

```text
README.md
```

## 3. Example benchmark config

Create a valid JSON example at:

```text
configs/benchmarks/verilog_eval_local_models.example.json
```

Requirements:

- Use dataset path `data/releases/release_v0.1_plus_verilog_eval_001/test.jsonl`.
- Include `include_rule_baseline: true`.
- Include two local endpoint placeholders using loopback addresses.
- Include no secrets.
- Use filesystem-safe model names.
- Use conservative defaults: temperature `0.0`, max tokens `8192`, timeout `120`, retries `1`, strict `false`.

## 4. Config README

Create `configs/benchmarks/README.md` explaining:

- copy the example before editing,
- keep generated benchmark outputs out of git,
- local model services can read submitted prompts,
- start with dry-run and a small limit,
- keep config values safe to commit.

## 5. First benchmark runbook

Create `docs/eval/first_local_benchmark_runbook.md` with short step-by-step sections:

1. prerequisites,
2. manual review reminder,
3. readiness command,
4. finalization command,
5. network-free dry-run benchmark command,
6. first small local benchmark command,
7. files to inspect,
8. generated files not to commit.

Include these commands.

### Readiness

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --strict \
  --json
```

### Finalization

```bash
python scripts/dataset/finalize_reviewed_batch.py \
  --batch-dir data/review/verilog_eval_batch_001 \
  --processed-output data/processed/verilog_eval_validated_v0.1.jsonl \
  --promotion-report data/reports/verilog_eval_validated_v0.1_report.json \
  --release-name release_v0.1_plus_verilog_eval_001 \
  --release-output-dir data/releases \
  --candidate-output data/eval/candidates/rule_baseline_verilog_eval_001.jsonl \
  --eval-output-dir data/eval/runs/rule_baseline_verilog_eval_001 \
  --allow-source-overlap \
  --json
```

### Dry-run benchmark

```bash
python scripts/eval/run_benchmark_suite.py \
  --config configs/benchmarks/verilog_eval_local_models.example.json \
  --output-dir /tmp/rtl_specializer_benchmark_dry_run \
  --limit 3 \
  --dry-run \
  --json \
  --overwrite
```

### Small local benchmark

```bash
cp configs/benchmarks/verilog_eval_local_models.example.json \
  configs/benchmarks/verilog_eval_local_models.local.json

python scripts/eval/run_benchmark_suite.py \
  --config configs/benchmarks/verilog_eval_local_models.local.json \
  --output-dir data/eval/benchmarks/verilog_eval_local_models_v0.1 \
  --limit 3 \
  --json \
  --overwrite
```

Tell the user to inspect summary Markdown/CSV, per-model candidate reports, parse failures, invalid candidates, safety failures, and lowest-scoring rows before drawing conclusions.

## 6. README update

Add short links to:

```text
docs/eval/first_local_benchmark_runbook.md
configs/benchmarks/README.md
```

## 7. Validation

Run:

```bash
python -m json.tool configs/benchmarks/verilog_eval_local_models.example.json >/tmp/rtl_specializer_benchmark_config_check.json
python -m pytest tests/eval/test_model_candidate_runner.py
python -m pytest tests/eval/test_benchmark_suite.py
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Do not run a real model service for this docs/config task.

## 8. Definition of done

Done only when:

- example benchmark config exists and is valid JSON,
- config README exists,
- first benchmark runbook exists,
- README links to both,
- no generated benchmark/candidate/eval outputs are committed,
- no real model calls are made,
- validation commands pass or any skipped command is clearly explained.

## 9. Codex implementation instructions

Implement this spec exactly. Keep it simple and docs/config-only unless a tiny typo fix is required.

After finishing, commit and push. Summarize changed files, commands run, validation results, and manual steps still required from the user.
