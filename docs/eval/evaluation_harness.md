# Evaluation harness

The evaluation harness scores local candidate `rtl_answer_v0.1` answers against dataset release rows. It is deterministic and offline: it does not train a model, run inference, call an LLM, download data, execute RTL, or run EDA tools.

## Candidate JSONL format

Each candidate line is:

```json
{
  "id": "golden_counter_bug_001",
  "answer": {
    "schema_version": "rtl_answer_v0.1",
    "task_type": "rtl_bug_review",
    "issue_summary": [],
    "time_reasoning": {},
    "space_reasoning": {},
    "safe_optimization": {},
    "functional_risk": [],
    "verification_plan": [],
    "claim_levels": {},
    "patch": {}
  },
  "metadata": {
    "model": "manual_baseline",
    "prompt_version": "baseline_v0.1"
  }
}
```

`id` must match a dataset row. `metadata` is optional. Duplicate candidate IDs are errors.

## Generate rule baseline candidates

```bash
python scripts/eval/make_baseline_candidates.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --output data/eval/candidates/rule_baseline_v0.1.jsonl \
  --json
```

The baseline is intentionally conservative. It uses dataset artifacts and extracted signal metadata, avoids copying reference answers verbatim, and does not claim verified correctness, area improvement, activity improvement, or power improvement.

## Generate local model candidates

The [local model candidate runner](model_candidate_runner.md) builds strict `rtl_answer_v0.1` prompts and can call a local OpenAI-compatible `/v1/chat/completions` endpoint:

```bash
python scripts/eval/run_model_candidates.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --output data/eval/candidates/local_model_v0.1.jsonl \
  --model local-model-name \
  --json
```

The endpoint defaults to localhost. Non-local endpoints require explicit authorization with `--allow-nonlocal-endpoint`. Use `--dry-run` to verify selection, output, and report behavior without making a network call.

Localhost does not imply a trusted model service: its process and operator can read submitted prompts and RTL. API keys are supplied only through named environment variables, never literal configuration values. Review data exposure before using the non-local opt-in.

## Generate hosted-model candidates from dataset messages

The [OpenAI-compatible candidate runner](openai_compatible_candidate_runner.md) sends only the dataset `system` and `user` messages to a user-supplied `/v1/chat/completions` base URL, never the reference assistant answer:

```bash
python scripts/eval/run_openai_compatible_candidates.py \
  --dataset data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl \
  --output data/eval/runs/rtlcoder_synthetic_active_model_smoke/candidates.jsonl \
  --base-url http://127.0.0.1:8000/v1 \
  --model active-model \
  --limit 3 \
  --json
```

This runner keeps parse failures and API failures as low-scoring candidate rows instead of dropping them, supports `--resume`, and can save raw model text under `--raw-output-dir`. API keys still come only from named environment variables.

For repeatable comparisons across multiple local models, use the [model benchmark suite](model_benchmark_suite.md). It generates or reuses per-model candidates, evaluates each candidate file with this harness, optionally includes `rule_baseline`, and aggregates scalar results into JSON, Markdown, and CSV summaries.

## Evaluate candidates

```bash
python scripts/eval/evaluate_answers.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --candidates data/eval/candidates/rule_baseline_v0.1.jsonl \
  --output-dir data/eval/runs/rule_baseline_v0.1 \
  --json
```

Outputs:

- `row_results.jsonl`
- `metrics.json`
- `report.md`
- `unmatched_candidates.jsonl`

## Rubric

Scores are deterministic and sum to 1.0:

- `schema_and_required_fields`: 0.20
- `issue_grounding`: 0.25
- `reasoning_quality`: 0.20
- `claim_safety`: 0.20
- `verification_plan`: 0.10
- `task_alignment`: 0.05

Safety failures are counted separately from the score. The evaluator reuses existing dataset validation and claim-safety checks where possible.

## Limitations

This harness is not semantic proof. It checks structure, conservative claim behavior, references to supplied artifacts/signals, and rubric heuristics. Generated candidate, raw, and evaluation outputs remain local and should not be committed without deliberate review.
