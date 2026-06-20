# First local benchmark runbook

This runbook moves one manually reviewed VerilogEval batch through readiness, finalization, a network-free benchmark dry-run, and a small local benchmark. It does not replace human review or establish RTL correctness.

## 1. Prerequisites

- Run commands from the repository root with Python and the project test dependencies available.
- Prepare `data/review/verilog_eval_batch_001/selected_rows.jsonl` and a manually edited `reviewed_rows.jsonl`.
- For the real benchmark only, operate the configured OpenAI-compatible services yourself. The tools do not start model servers.
- Copy and edit the example config only after the dry-run. Keep secrets out of JSON; local services can read submitted prompts and RTL.

## 2. Complete manual review

Review every selected row yourself. Resolve placeholders, unsupported claims, and incomplete answers before readiness. Do not promote rows merely to make the gate pass.

## 3. Check readiness

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --strict \
  --json
```

Continue only when the strict readiness report passes for every intended row.

## 4. Finalize the reviewed batch

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

Finalization should create the release test split referenced by the benchmark example. Inspect its report before continuing.

## 5. Run the network-free benchmark dry-run

```bash
python scripts/eval/run_benchmark_suite.py \
  --config configs/benchmarks/verilog_eval_local_models.example.json \
  --output-dir /tmp/rtl_specializer_benchmark_dry_run \
  --limit 3 \
  --dry-run \
  --json \
  --overwrite
```

Dry-run builds placeholder candidates without contacting either endpoint. Confirm the resolved config and expected model jobs before inference.

## 6. Run a small local benchmark

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

Edit the copied model identifiers and loopback endpoints first. Verify that both local services are intentionally running; the suite will not start them. Keep `--limit 3` until the outputs look sound.

## 7. Inspect the results

Start with:

- `benchmark_summary.md` for the ranked comparison and failures;
- `benchmark_summary.csv` for the scalar results;
- `models/<name>/candidate_report.json` for per-model generation status;
- each model's candidate report for parse failures and invalid candidates;
- summary and evaluator reports for safety failures;
- evaluator `row_results.jsonl` files for the lowest-scoring rows.

These scores are deterministic heuristics, not proof of functional correctness. Investigate failures and weak rows before drawing conclusions or increasing the limit.

## 8. Keep generated files out of git

Do not commit review reports, processed/release outputs, benchmark summaries, candidate JSONL, raw model responses, or evaluator outputs. This includes content under `data/review/`, `data/reports/`, `data/releases/`, `data/eval/candidates/`, `data/eval/runs/`, and `data/eval/benchmarks/`, plus the sibling candidate/evaluation directories derived from a benchmark output path.

Run `git status --short` before every commit and stage only the intended source documentation or sanitized configuration files.
