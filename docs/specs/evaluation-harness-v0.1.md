# Feature Spec: Evaluation Harness v0.1

## 1. Goal

Build a local, deterministic evaluation harness for `RTLSpecializer` dataset releases.

The repo can now create golden rows, import public draft rows, review and promote public rows, and assemble reproducible dataset releases with leakage checks. The next highest-value step is to evaluate candidate assistant answers against release rows before any fine-tuning work begins.

This feature creates an offline evaluation layer:

```text
data/releases/<release-name>/test.jsonl
  + candidate answer JSONL
  -> schema validation
  -> deterministic rubric scoring
  -> safety/claim checks
  -> per-row results
  -> aggregate metrics
  -> evaluation report
```

The harness must not call external LLMs, train a model, execute RTL, run EDA tools, or require GPUs. It scores candidate outputs using deterministic checks against the dataset row and reference answer.

The goal is to establish a baseline evaluation contract so future base-model, fine-tuned-model, and rule-based outputs can be compared consistently.

## 2. Non-goals

Do not build:

- Model training.
- QLoRA, DoRA, DPO, or inference scripts.
- External LLM API calls.
- EDA execution.
- RTL simulation, synthesis, equivalence, or toggle analysis.
- Web UI.
- Dataset downloads.
- Human preference labeling.
- A learned judge model.
- New dataset schema versions.

## 3. Assumptions

- Input evaluation rows use `dataset_v0.1`.
- Reference answers are the assistant messages inside the dataset rows.
- Candidate answers are supplied as local JSONL.
- Candidate answer format is explicit and simple.
- Deterministic rubric scoring is enough for v0.1.
- The harness should support partial candidate outputs and report missing rows.
- Evaluation should usually run on release `test.jsonl`, but any dataset JSONL file may be used.
- Python standard library only.

## 4. User stories

- As a model trainer, I want a baseline score before fine-tuning, so that I know whether training improves anything.
- As a dataset maintainer, I want deterministic scoring, so that changes in data and prompts can be compared across commits.
- As an RTL reviewer, I want claim-safety failures to be counted separately, so that unsafe confident answers are penalized.
- As a project lead, I want per-task and per-domain metrics, so that weak areas are visible before scaling data.
- As a future inference pipeline developer, I want a stable candidate-output format, so that any model runner can plug into the evaluator.

## 5. UX / UI requirements

No graphical UI.

Add CLI:

```bash
python scripts/eval/evaluate_answers.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --candidates data/eval/candidates/example_answers.jsonl \
  --output-dir data/eval/runs/baseline_rule_v0.1
```

Supported options:

```text
--dataset <jsonl>
--candidates <jsonl>
--output-dir <dir>
--run-name <optional name>
--strict
--json
```

### Success state

```text
Evaluation completed.

Dataset rows: 12
Candidate rows: 12
Matched rows: 12
Missing candidates: 0
Extra candidates: 0
Mean score: 0.78
Safety failures: 0
Output: data/eval/runs/baseline_rule_v0.1
```

Exit code: `0`.

### Partial success state

If some candidates are missing but scoring can proceed:

```text
Evaluation completed with warnings.

Dataset rows: 12
Candidate rows: 10
Matched rows: 10
Missing candidates: 2
Extra candidates: 0
Mean score: 0.66
Safety failures: 0
```

Exit code: `0` by default, `1` when `--strict` is set.

### Failure state

```text
Evaluation failed.

Errors:
- candidate answer for row golden_counter_bug_001 is not valid rtl_answer_v0.1 shape
```

Exit code: `1`.

### JSON output

When `--json` is set, print:

```json
{
  "ok": true,
  "dataset_rows": 12,
  "candidate_rows": 12,
  "matched_rows": 12,
  "missing_candidates": 0,
  "extra_candidates": 0,
  "mean_score": 0.78,
  "safety_failures": 0,
  "output_dir": "data/eval/runs/baseline_rule_v0.1",
  "errors": [],
  "warnings": []
}
```

## 6. Functional requirements

### FR-1: Define candidate answer JSONL format

Candidate file format is JSONL. Each line must be an object:

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

Rules:

- `id` must match dataset row ID.
- `answer` must be a candidate `rtl_answer_v0.1` object.
- `metadata` is optional but recommended.
- Extra fields are allowed but ignored.
- Duplicate candidate IDs are errors.

### FR-2: Add evaluator CLI

Create:

```text
scripts/eval/evaluate_answers.py
```

The CLI must:

- load dataset JSONL using existing JSONL helpers,
- validate dataset using `validate_dataset_file(..., strict=True)`,
- load candidate JSONL,
- match candidates by row ID,
- validate candidate answer shape using existing answer validation logic or a reusable helper,
- compute per-row deterministic scores,
- write per-row results,
- write aggregate metrics,
- write Markdown report,
- return clear exit codes.

### FR-3: Add reusable evaluation module

Create:

```text
scripts/eval/evaluator.py
```

Suggested functions:

```python
def load_candidate_answers(path: Path) -> CandidateLoadResult:
    ...

def evaluate_answer(dataset_row: dict, candidate_answer: dict) -> RowEvalResult:
    ...

def evaluate_dataset(dataset_rows: list[dict], candidates: dict[str, dict]) -> EvalResult:
    ...
```

Use dataclasses where helpful. Keep functions testable without CLI.

### FR-4: Candidate answer validation

A candidate answer must satisfy at least:

- object type,
- `schema_version == rtl_answer_v0.1`,
- `task_type` matches dataset task type,
- required answer fields exist,
- `claim_levels` has correctness, area, activity, power,
- claim levels are valid enum values,
- `issue_summary` is non-empty,
- `verification_plan` includes lint or compile,
- `patch` object exists.

Use the existing dataset validator if possible by constructing a temporary row with the original dataset task and candidate answer.

### FR-5: Deterministic rubric scoring

Each matched row receives a score from 0.0 to 1.0.

Use a simple weighted rubric:

```text
schema_and_required_fields: 0.20
issue_grounding:           0.25
reasoning_quality:         0.20
claim_safety:              0.20
verification_plan:         0.10
task_alignment:            0.05
```

The exact scoring must be documented in code and docs.

### FR-6: Schema and required fields score

Award up to 0.20:

- required top-level answer fields present,
- valid schema version,
- task type matches,
- valid claim level domains and values,
- patch object present.

A structurally invalid candidate may still produce a row result, but score should be low and errors must be reported.

### FR-7: Issue grounding score

Award up to 0.25 based on:

- candidate has at least one issue,
- issue has non-empty `issue`, `severity`, and `evidence`,
- evidence has non-empty `signal_names`,
- evidence code-location module matches or is compatible with the dataset top module/reference evidence module,
- evidence reason is concrete and not generic,
- candidate mentions at least one reference signal or supplied artifact field.

This is deterministic text/structure matching, not semantic proof.

### FR-8: Reasoning quality score

Award up to 0.20 based on:

- `time_reasoning.clock_cycle_behavior` is non-empty and not generic,
- latency/state/reset risks are present,
- `space_reasoning.hardware_resources_involved` is non-empty,
- area/activity risks mention evidence limitations, synthesis, VCD, toggle, or tool requirements.

### FR-9: Claim safety score

Award up to 0.20:

- start with full 0.20,
- subtract for unsupported power, area, activity, or verified correctness claims using existing `claim_safety` and evidence-status checks,
- subtract for `verified` without passing evidence,
- subtract for `tool_supported` without meaningful evidence,
- minimum 0.0.

Claim-safety failures must also be counted separately in aggregate metrics.

### FR-10: Verification plan score

Award up to 0.10:

- includes lint/compile,
- includes simulation when correctness behavior is discussed,
- includes synthesis for area/resource claims,
- includes VCD/toggle/activity comparison for activity claims,
- mentions power report if any power claim is present.

### FR-11: Task alignment score

Award up to 0.05:

- answer `task_type` matches row task,
- answer style fits task type,
- unsafe optimization rejection tasks should reject unsafe changes,
- before/after judgment tasks should compare before/after artifacts,
- tool-report explanation tasks should discuss supplied report artifacts.

### FR-12: Output files

Create output directory:

```text
data/eval/runs/<run-name>/
  row_results.jsonl
  metrics.json
  report.md
  unmatched_candidates.jsonl
```

`row_results.jsonl` must contain:

```json
{
  "id": "golden_counter_bug_001",
  "score": 0.82,
  "subscores": {
    "schema_and_required_fields": 0.20,
    "issue_grounding": 0.20,
    "reasoning_quality": 0.17,
    "claim_safety": 0.20,
    "verification_plan": 0.08,
    "task_alignment": 0.05
  },
  "errors": [],
  "warnings": [],
  "safety_failures": []
}
```

`metrics.json` must include:

- dataset rows,
- candidate rows,
- matched rows,
- missing candidate count,
- extra candidate count,
- mean score,
- median score,
- min score,
- max score,
- score by task type,
- score by source,
- score by design family,
- safety failure counts,
- error counts.

`report.md` must include:

- run summary,
- input paths,
- scoring rubric,
- aggregate metrics,
- weakest rows table,
- safety failure summary,
- limitations.

### FR-13: Add simple baseline candidate generator

Create a deterministic rule-based baseline generator for smoke testing:

```text
scripts/eval/make_baseline_candidates.py
```

CLI:

```bash
python scripts/eval/make_baseline_candidates.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --output data/eval/candidates/rule_baseline_v0.1.jsonl
```

Behavior:

- For each dataset row, emit a conservative `rtl_answer_v0.1` candidate.
- The baseline may be weak but must be valid enough to evaluate.
- It must not claim verification, area improvement, activity improvement, or power improvement.
- It must not copy the reference answer verbatim except for schema/task-required fields and artifact-derived signal names.

This baseline exists so CI/tests can exercise the evaluator without a model.

### FR-14: Add tests

Create:

```text
tests/eval/test_evaluator.py
```

Required tests:

- candidate loader accepts valid candidate JSONL,
- duplicate candidate IDs fail,
- missing candidates are reported,
- extra candidates are reported,
- structurally invalid candidate receives low score and errors,
- unsafe power claim creates safety failure,
- perfect/reference candidate scores high,
- weak baseline candidate scores lower than reference candidate,
- metrics JSON includes task/source/design-family breakdowns,
- CLI `--json` output is parseable,
- baseline generator creates valid candidate JSONL.

### FR-15: Add docs

Create:

```text
docs/eval/evaluation_harness.md
```

Update:

```text
README.md
docs/dataset/release_workflow.md
```

Docs must explain:

- candidate answer format,
- how to generate baseline candidates,
- how to run evaluation,
- what the rubric scores mean,
- why deterministic scoring is not semantic proof,
- why this happens before fine-tuning.

## 7. Technical requirements

### 7.1 Architecture

Add a new `scripts/eval/` package:

```text
scripts/eval/__init__.py
scripts/eval/evaluator.py
scripts/eval/evaluate_answers.py
scripts/eval/make_baseline_candidates.py
```

Keep it independent from model inference. It should consume JSONL files only.

### 7.2 Validation reuse

Prefer reusing existing dataset validators by creating temporary candidate rows in memory or temp files. Avoid duplicating large validation logic.

### 7.3 Security

- Treat dataset/candidate content as untrusted data.
- Do not execute RTL.
- Do not execute candidate text.
- Do not shell out.
- Do not download data.
- Do not call LLM APIs.
- Do not include credentials.

### 7.4 Dependencies

Use standard library only.

Allowed modules include:

```text
argparse
json
pathlib
dataclasses
typing
collections
statistics
copy
re
sys
```

## 8. Files likely involved

Create:

```text
scripts/eval/__init__.py
scripts/eval/evaluator.py
scripts/eval/evaluate_answers.py
scripts/eval/make_baseline_candidates.py
docs/eval/evaluation_harness.md
tests/eval/test_evaluator.py
```

Modify:

```text
README.md
docs/dataset/release_workflow.md
```

Do not modify unrelated files.

## 9. Data model

No database.

Evaluation inputs and outputs are JSONL/JSON/Markdown files.

Candidate JSONL format is defined in FR-1.

## 10. API contract

### Make baseline candidates

```bash
python scripts/eval/make_baseline_candidates.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --output data/eval/candidates/rule_baseline_v0.1.jsonl \
  --json
```

Response:

```json
{
  "ok": true,
  "dataset_rows": 12,
  "candidate_rows": 12,
  "output": "data/eval/candidates/rule_baseline_v0.1.jsonl",
  "errors": [],
  "warnings": []
}
```

### Evaluate answers

```bash
python scripts/eval/evaluate_answers.py \
  --dataset data/releases/release_v0.1/test.jsonl \
  --candidates data/eval/candidates/rule_baseline_v0.1.jsonl \
  --output-dir data/eval/runs/rule_baseline_v0.1 \
  --json
```

Response:

```json
{
  "ok": true,
  "dataset_rows": 12,
  "candidate_rows": 12,
  "matched_rows": 12,
  "missing_candidates": 0,
  "extra_candidates": 0,
  "mean_score": 0.55,
  "safety_failures": 0,
  "output_dir": "data/eval/runs/rule_baseline_v0.1",
  "errors": [],
  "warnings": []
}
```

## 11. Edge cases

Handle:

- missing dataset file,
- malformed dataset JSONL,
- invalid dataset row,
- empty candidate file,
- malformed candidate JSONL,
- duplicate candidate IDs,
- missing candidates,
- extra candidates,
- candidate answer not object,
- candidate answer wrong task type,
- candidate answer missing claim levels,
- candidate answer with unsupported power claim,
- candidate answer with `verified` but no pass evidence,
- empty test split,
- very small dataset,
- Unicode comments/signals.

## 12. Testing plan

Run:

```bash
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Manual smoke flow:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name test_release \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir /tmp/rtl_specializer_releases \
  --seed 7 \
  --allow-source-overlap \
  --json

python scripts/eval/make_baseline_candidates.py \
  --dataset /tmp/rtl_specializer_releases/test_release/test.jsonl \
  --output /tmp/rtl_specializer_eval/rule_baseline.jsonl \
  --json

python scripts/eval/evaluate_answers.py \
  --dataset /tmp/rtl_specializer_releases/test_release/test.jsonl \
  --candidates /tmp/rtl_specializer_eval/rule_baseline.jsonl \
  --output-dir /tmp/rtl_specializer_eval/run_rule_baseline \
  --json
```

## 13. Definition of done

Done only when:

- Candidate JSONL format is documented.
- Baseline generator produces valid candidate answer JSONL.
- Evaluator scores candidate answers deterministically.
- Evaluator writes row results, metrics, unmatched candidates, and report.
- Safety failures are counted separately.
- Missing/extra candidates are reported.
- Tests cover loader, scoring, safety, metrics, CLI JSON, and baseline generation.
- Docs explain usage and limitations.
- No model training, LLM calls, EDA execution, downloads, or external services are introduced.

## 14. Codex implementation instructions

Implement this spec exactly.

Focus only on a deterministic local evaluation harness and baseline candidate generator.

Do not add model training, model inference, external LLM calls, downloads, EDA execution, or schema changes.

Use standard-library Python only.

Run:

```bash
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run the manual smoke flow in the testing plan.

After finishing, commit and push. Summarize changed files, commands run, test results, and tradeoffs.