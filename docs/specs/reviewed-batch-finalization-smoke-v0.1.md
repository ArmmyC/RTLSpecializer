# Feature Spec: Reviewed Batch Finalization Smoke v0.1

## 1. Goal

Add a local workflow that finalizes a manually reviewed public batch only after the readiness checker says the batch is ready.

The repository now has:

- local public draft import,
- VerilogEval review-batch preparation,
- review readiness checking,
- public draft promotion,
- deterministic release assembly,
- conservative baseline candidate generation,
- deterministic evaluation.

The next highest-value improvement is to connect those existing steps into one guarded local finalization command.

The intended flow is:

```text
reviewed_rows.jsonl
  -> readiness check must pass
  -> promote reviewed rows into processed JSONL
  -> validate processed JSONL
  -> build local release
  -> generate conservative baseline candidates
  -> evaluate baseline
  -> write finalization summary
```

This command is a local smoke/finalization workflow. It must not perform human review automatically and must not make unreviewed rows ready.

## 2. Non-goals

Do not build:

- model training,
- model inference,
- LLM calls,
- automatic answer editing,
- automatic human review,
- external dataset downloads,
- EDA execution,
- RTL simulation,
- synthesis,
- equivalence checking,
- toggle/power analysis,
- schema changes,
- publishing release artifacts,
- committing generated data automatically.

## 3. Preconditions

The batch directory should exist locally, for example:

```text
data/review/verilog_eval_batch_001/
```

It should contain:

```text
selected_rows.jsonl
reviewed_rows.jsonl
```

The user must manually edit `reviewed_rows.jsonl` before finalization.

The readiness checker must report `all_rows_ready: true` unless an explicit unsafe override is added later. For this v0.1, do not add such an override.

## 4. CLI UX

Add:

```text
scripts/dataset/finalize_reviewed_batch.py
```

Example:

```bash
python scripts/dataset/finalize_reviewed_batch.py \
  --batch-dir data/review/verilog_eval_batch_001 \
  --processed-output data/processed/verilog_eval_validated_v0.1.jsonl \
  --promotion-report data/reports/verilog_eval_validated_v0.1_report.json \
  --release-name release_v0.1_plus_verilog_eval_001 \
  --release-output-dir data/releases \
  --eval-output-dir data/eval/runs/rule_baseline_verilog_eval_001 \
  --candidate-output data/eval/candidates/rule_baseline_verilog_eval_001.jsonl \
  --json
```

Supported options:

```text
--batch-dir <path>                 contains selected_rows.jsonl and reviewed_rows.jsonl
--processed-output <path>           promoted validated rows output
--promotion-report <path>           promotion report JSON
--release-name <name>               release directory name
--release-output-dir <path>         release parent directory
--candidate-output <path>           conservative baseline candidate JSONL
--eval-output-dir <path>            evaluation run directory
--golden-input <path>               default data/golden/golden_v0.1.jsonl
--seed <int>                        default 7
--allow-source-overlap              passed through to release builder when needed
--json                              print JSON summary
```

The command should write local reports under the batch directory:

```text
readiness_report.json
readiness_report.md
finalization_summary.json
finalization_summary.md
```

All paths under `data/review/`, `data/reports/`, `data/releases/`, and `data/eval/` are ignored by default and should remain local unless intentionally published later.

## 5. Functional requirements

### FR-1: Readiness gate first

Before promotion, run the readiness checker in strict/all-ready mode using:

```text
<batch-dir>/selected_rows.jsonl
<batch-dir>/reviewed_rows.jsonl
```

Write:

```text
<batch-dir>/readiness_report.json
<batch-dir>/readiness_report.md
```

If readiness does not report `all_rows_ready: true`, stop immediately.

Do not promote partial batches in v0.1.

### FR-2: Promotion

If readiness passes, promote rows using the existing promotion helpers or CLI-equivalent module logic.

Inputs:

```text
<batch-dir>/reviewed_rows.jsonl
```

Outputs:

```text
--processed-output
--promotion-report
<processed-output>.rejected.jsonl
```

Requirements:

- target status must be `validated`,
- do not allow stub answers,
- run in strict mode so any rejected row fails finalization,
- validate promoted output strictly.

### FR-3: Release assembly

If promotion succeeds, build a deterministic local release using:

```text
--golden-input
--processed-output
```

with:

```text
--release-name
--release-output-dir
--seed
```

The finalization command should use existing release-building code, not duplicate release logic.

For tiny single-source smoke releases, support an explicit `--allow-source-overlap` passthrough.

### FR-4: Conservative baseline generation

After release assembly succeeds, generate conservative rule-baseline candidates for the release test split.

Input:

```text
<release-output-dir>/<release-name>/test.jsonl
```

Output:

```text
--candidate-output
```

Use existing baseline generation code or invoke the existing module logic directly.

### FR-5: Evaluation

Evaluate the conservative baseline candidates against the release test split.

Inputs:

```text
<release-output-dir>/<release-name>/test.jsonl
--candidate-output
```

Output:

```text
--eval-output-dir
```

Use the existing deterministic evaluation harness.

### FR-6: Summary reports

Write:

```text
<batch-dir>/finalization_summary.json
<batch-dir>/finalization_summary.md
```

The JSON summary should include:

```json
{
  "ok": true,
  "batch_dir": "...",
  "readiness": {"all_rows_ready": true, "ready_rows": 10},
  "promotion": {"accepted_rows": 10, "rejected_rows": 0},
  "release": {"release_name": "...", "output_dir": "..."},
  "evaluation": {"mean_score": 0.0, "rows_evaluated": 0},
  "outputs": {
    "processed_output": "...",
    "promotion_report": "...",
    "release_dir": "...",
    "candidate_output": "...",
    "eval_output_dir": "..."
  },
  "errors": [],
  "warnings": []
}
```

Markdown should clearly explain:

- readiness status,
- promoted row count,
- rejected row count,
- release output path,
- baseline evaluation output path,
- that this is still local until the user intentionally publishes/commits approved artifacts,
- next suggested manual checks.

### FR-7: Safety and local-only behavior

Treat all dataset content as untrusted data.

Do not execute RTL, testbenches, shell commands embedded in data, or generated text.

Do not call external services.

Do not download anything.

Do not run EDA tools.

Do not commit generated outputs.

### FR-8: Tests

Add tests under:

```text
tests/dataset/test_finalize_reviewed_batch.py
```

Required tests:

- finalization stops if readiness fails because reviewed rows are unchanged,
- finalization promotes a ready synthetic fixture batch,
- strict promotion failure stops release/evaluation,
- release directory is created only after promotion succeeds,
- candidate output is created only after release succeeds,
- evaluation output is created only after candidate generation succeeds,
- JSON summary is written and parseable,
- Markdown summary contains local-only warning,
- CLI `--json` output is parseable,
- no generated files are written outside requested output paths.

Use synthetic fixtures only. Do not add real VerilogEval content.

### FR-9: Docs

Create:

```text
docs/dataset/finalize_reviewed_batch_workflow.md
```

Update:

```text
README.md
docs/dataset/review_readiness_workflow.md
docs/dataset/verilog_eval_review_workflow.md
```

Docs must explain:

```text
prepare review batch -> manual review -> readiness check -> finalization smoke -> optional commit/publish decision
```

The docs must say finalization does not replace human review and does not certify the dataset scientifically; it only verifies that the local pipeline can promote, release, and evaluate the reviewed batch.

## 6. Architecture requirements

Prefer a reusable module:

```text
scripts/dataset/finalize_reviewed_batch.py
```

If practical, factor implementation into functions within that file:

```python
def finalize_batch(config: FinalizationConfig) -> dict[str, Any]:
    ...
```

Reuse existing project helpers/modules instead of shelling out where possible.

Potential existing code to reuse:

```text
scripts.dataset.review_readiness
scripts.dataset.review_promotion
scripts.dataset.release
scripts.eval.make_baseline_candidates
scripts.eval.evaluator
```

If existing CLI code is hard to reuse directly, call internal functions from modules rather than launching subprocesses.

Use standard library only.

## 7. Edge cases

Handle:

- missing batch directory,
- missing selected_rows.jsonl,
- missing reviewed_rows.jsonl,
- readiness not all ready,
- promotion accepts zero rows,
- promotion rejects any row,
- release builder fails due to leakage or invalid rows,
- empty release test split,
- candidate generation fails,
- evaluation fails,
- output paths already exist,
- paths accidentally pointing inside `.local_data`,
- user tries to overwrite processed output without explicit `--force`.

Add `--force` only for outputs generated by this finalization command. Default should protect existing outputs.

## 8. Testing plan

Run:

```bash
python -m pytest tests/dataset/test_finalize_reviewed_batch.py
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Run a fixture smoke command with temporary output paths, for example:

```bash
python scripts/dataset/finalize_reviewed_batch.py \
  --batch-dir /tmp/rtl_specializer_ready_fixture_batch \
  --processed-output /tmp/rtl_specializer_processed_fixture.jsonl \
  --promotion-report /tmp/rtl_specializer_processed_fixture_report.json \
  --release-name release_fixture_reviewed_batch \
  --release-output-dir /tmp/rtl_specializer_releases \
  --candidate-output /tmp/rtl_specializer_candidates_fixture.jsonl \
  --eval-output-dir /tmp/rtl_specializer_eval_fixture \
  --allow-source-overlap \
  --json \
  --force
```

The test may create the temporary ready fixture batch first.

If the user's local review batch exists and readiness passes, optionally run the same command on:

```text
data/review/verilog_eval_batch_001/
```

Do not fail CI if the local review batch is absent or not ready.

## 9. Definition of done

Done only when:

- Finalization CLI exists.
- Finalization stops before promotion if readiness is not all-ready.
- Promotion uses existing gates and strict behavior.
- Release assembly uses existing release logic.
- Baseline generation uses existing conservative baseline logic.
- Evaluation uses existing deterministic evaluation logic.
- JSON and Markdown finalization summaries are written.
- Tests cover success, readiness failure, promotion failure, output path safety, and CLI JSON.
- Docs explain the full workflow.
- No model calls, EDA calls, downloads, training, schema changes, or automatic commits are introduced.

## 10. Codex implementation instructions

Implement this spec exactly.

This feature only finalizes a batch after human review and readiness success. It must not edit answers, perform review, or make not-ready rows ready.

After finishing, commit and push. Summarize changed files, commands run, test results, smoke results, and tradeoffs.
