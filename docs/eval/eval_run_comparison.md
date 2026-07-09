# Eval run comparison

Use the eval-run comparison tools before fine-tuning to answer two different questions:

```text
How do complete eval runs compare?        -> compare_eval_runs.py
How do two candidate files differ by row? -> inspect_candidate_differences.py
```

## Compare whole eval runs

`compare_eval_runs.py` reads each run directory's `metrics.json` and `row_results.jsonl` and summarizes:

- matched rows
- mean, median, min, and max score
- safety failures
- error counts
- score by task type
- weakest rows per run
- pairwise row overlap
- rows with the largest score spread across runs

Example:

```bash
python scripts/eval/compare_eval_runs.py \
  --runs \
    data/eval/runs/rtlcoder_synthetic_rule_baseline \
    data/eval/runs/rtlcoder_synthetic_active_model_smoke \
    data/eval/runs/rtlcoder_synthetic_finetuned_model \
  --output-md data/eval/reports/run_comparison.md \
  --output-json data/eval/reports/run_comparison.json \
  --json
```

This does not require identical row coverage. It reports overlap and which rows are missing from each run relative to the union.

## Inspect candidate differences

`inspect_candidate_differences.py` compares two candidate JSONL files against the dataset row-by-row and highlights whether the answers differ semantically or only cosmetically.

It reports, for shared IDs:

- `issue_summary` text
- `signal_names`
- `claim_levels`
- `evidence_used`
- `limitations`
- `patch.provided`
- whether the answer mentions the mutation type from `source_id` or `mutation_summary`
- whether the answer mentions `mutated_signal_names`

It also detects repeated generic answers using exact and near-duplicate answer checks.

Example:

```bash
python scripts/eval/inspect_candidate_differences.py \
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl \
  --candidates-a data/eval/runs/rtlcoder_synthetic_rule_baseline/candidates.jsonl \
  --name-a rule_baseline \
  --candidates-b data/eval/runs/rtlcoder_synthetic_active_model_smoke/candidates.jsonl \
  --name-b active_model \
  --output-md data/eval/reports/active_vs_rule.md \
  --output-json data/eval/reports/active_vs_rule.json \
  --json
```

## What to look for before fine-tuning

- If the active model score is higher only because schema and safety fields improved, the row-difference report will still show generic issue text and weak mutation mentions.
- If the model is really finding the intended synthetic bug, you should see issue text, signal names, and mutation or mutated-signal mentions differ from the conservative rule baseline in row-specific ways.
- If duplicate analysis shows many exact or near-duplicate answers, the model may be emitting one safe template repeatedly rather than grounding on the supplied task.

These reports are deterministic inspection tools. They do not prove RTL correctness and they do not execute RTL, call another model, or fine-tune anything.
