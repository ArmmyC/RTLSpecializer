# Review batch triage workflow

The review-batch triage assistant is a local, deterministic diagnostic step that sits before readiness checking. It compares `selected_rows.jsonl` and the human-edited `reviewed_rows.jsonl`, then reports data-quality risks without changing either file.

It does not call models, execute RTL or testbenches, run EDA tools, approve rows, promote rows, or make human review decisions.

## When to run it

Run triage after editing a local review batch and before the stricter readiness checker:

```bash
python scripts/dataset/triage_review_batch.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/triage_report.json \
  --output-md data/review/verilog_eval_batch_001/triage_report.md \
  --json
```

The generated reports stay local under `data/review/` and must not be committed.

## What it flags

The report identifies missing, extra, or duplicate IDs; malformed message order; user messages accidentally containing answers; duplicated user/assistant content; incomplete or placeholder task artifacts; and a small set of answer-quality risks.

It also flags empty issue summaries as minor. An empty summary can be intentional for a no-bug review, but a human reviewer should normally decide whether a grounded, low-severity no-bug finding would make the training example clearer.

Reset and evidence wording checks are heuristics only. A flag means “check this against the source artifacts,” not “the design is wrong.”

## Strict mode

Use `--strict` when you want a nonzero exit status for critical or important findings:

```bash
python scripts/dataset/triage_review_batch.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --strict \
  --json
```

Without `--strict`, parseable inputs return success even when triage finds row-level issues, so the human reviewer can inspect the reports.

## Next step

Resolve triage findings through manual review, then run the [review readiness workflow](review_readiness_workflow.md). Triage never replaces the readiness gate or human approval.
