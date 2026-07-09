# Human Task Spec: First VerilogEval Review Session v0.1

## 1. Goal

Complete the first real human review session for the local VerilogEval review batch.

This is not a Codex implementation task. The goal is for the human reviewer to edit the local `reviewed_rows.jsonl` file so the batch can pass strict readiness and move toward finalization and first benchmark results.

## 2. Why this is next

The repository now has enough infrastructure for the first small benchmark path:

```text
prepare review batch -> human review -> readiness -> finalization -> benchmark dry-run -> local benchmark
```

More tooling is lower value until at least a few rows are manually reviewed and real readiness/benchmark results exist.

## 3. Human-owned inputs

Expected local files:

```text
data/review/verilog_eval_batch_001/selected_rows.jsonl
data/review/verilog_eval_batch_001/reviewed_rows.jsonl
data/review/verilog_eval_batch_001/review_packet/
```

These are generated local review files and normally must not be committed.

## 4. Human review steps

For each selected row:

1. Open the corresponding `.review.md` packet under `review_packet/`.
2. Read the task prompt/specification.
3. Inspect RTL/testbench artifacts as text.
4. Edit the matching row in `reviewed_rows.jsonl`.
5. Remove placeholder/stub phrasing.
6. Keep correctness, area, activity, and power claims conservative unless evidence exists in the row.
7. Include a practical lint/compile/focused-simulation verification plan where appropriate.
8. Do not change row IDs.
9. Save the file.

Use:

```text
docs/dataset/manual_review_session_guide.md
docs/dataset/rtl_answer_review_checklist.md
```

## 5. What not to delegate to Codex

Do not ask Codex to silently produce reviewed answers.

Codex may help explain schema fields or point out readiness errors, but the human reviewer must decide the final reviewed content.

Do not use Codex to bypass gates, weaken validation, edit IDs, or mark unreviewed rows as ready.

## 6. Readiness command after editing

Run:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --strict \
  --json
```

If readiness fails, fix the reviewed rows and rerun the command.

## 7. Done condition

This human task is done when:

- every intended selected row has a human-reviewed answer,
- strict readiness passes,
- license/provenance has been checked before promotion/finalization,
- generated review/readiness outputs remain uncommitted,
- the next runbook step can proceed to finalization.

## 8. Next after done

After readiness passes, follow:

```text
docs/eval/first_local_benchmark_runbook.md
```

Start with finalization, then benchmark dry-run, then a small local benchmark.

## 9. Codex usage note

There is no Codex implementation prompt for this human task.

The next useful Codex prompt should happen only after the human review is done or readiness fails with specific errors that need interpretation.
