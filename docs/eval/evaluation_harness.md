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

This harness is not semantic proof. It checks structure, conservative claim behavior, references to supplied artifacts/signals, and rubric heuristics. It is meant to provide a stable baseline before fine-tuning or comparing future model outputs.
