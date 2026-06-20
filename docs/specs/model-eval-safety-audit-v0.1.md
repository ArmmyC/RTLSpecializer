# Improvement Spec: Model Eval Safety Audit v0.1

## 1. Goal

Audit and harden the local model candidate runner and benchmark suite before relying on them for real local model comparisons.

The repository now contains network-capable evaluation helpers:

```text
scripts/eval/run_model_candidates.py
scripts/eval/model_candidate_runner.py
scripts/eval/run_benchmark_suite.py
scripts/eval/benchmark_suite.py
```

These tools are intended for local/OpenAI-compatible endpoints with localhost-only defaults, dry-run support, strict JSON parsing, deterministic evaluation, and output-safety checks. Because they can send dataset task content to an HTTP endpoint when explicitly invoked, they need a focused safety and test audit.

This is primarily an audit/hardening task, not a feature-expansion task.

## 2. Source specs to compare against

Read and compare the current implementation against:

```text
docs/specs/local-model-candidate-runner-v0.1.md
docs/specs/model-benchmark-suite-v0.1.md
```

Also follow:

```text
AGENTS.md
docs/codex/code_review.md
```

## 3. Non-goals

Do not add:

- real model calls in tests,
- OpenAI API calls,
- external endpoint calls,
- dataset downloads,
- model downloads,
- EDA tools,
- RTL simulation/synthesis/equivalence/toggle/power analysis,
- training or fine-tuning,
- new dataset schemas,
- web UI,
- benchmark result claims beyond deterministic evaluator output.

Do not commit generated candidate/eval outputs.

## 4. Audit requirements

### AR-1: Endpoint safety

Verify and, if needed, harden:

- default endpoint is local-only,
- accepted default hosts are only `127.0.0.1`, `localhost`, and `::1`,
- non-local endpoints require explicit opt-in,
- benchmark suite requires dual opt-in for non-local endpoints:
  - model config allows it,
  - CLI flag allows it,
- endpoint URLs reject credentials,
- endpoint URLs reject query strings and fragments,
- endpoint path must be exactly `/v1/chat/completions`, allowing only harmless trailing slash normalization if already implemented.

### AR-2: Secret handling

Verify and, if needed, harden:

- API key values are read only from environment variables,
- API key values are never written to candidate rows, resolved benchmark config, reports, Markdown, logs, or error messages,
- unsupported config fields are rejected rather than copied into reports,
- examples use environment variable names, not literal secrets.

### AR-3: Prompt/data safety

Verify and, if needed, harden:

- prompts include task inputs needed for answer generation,
- prompts do not include reference/golden assistant answers,
- model output is treated as untrusted text,
- model output is never executed,
- raw output filenames are sanitized and collision-resistant,
- raw output directories are rejected inside `.local_data`, symlinked directories, dangerous roots, or overlapping paths.

### AR-4: Candidate parsing and validation

Verify and, if needed, harden:

- direct JSON object answer is accepted,
- first valid surrounding JSON object can be extracted conservatively,
- arrays/scalars are rejected,
- full candidate rows are rejected when the tool expects only `rtl_answer_v0.1` answer content,
- invalid answer objects are represented with explicit failure metadata,
- `--strict` fails if any generated row has parse or validation errors,
- dry-run candidates are clearly marked and do not pretend to be valid model answers.

### AR-5: Output safety and resume/overwrite behavior

Verify and, if needed, harden:

- `--resume` and `--overwrite` are mutually exclusive,
- existing outputs fail unless resume/overwrite behavior is explicit,
- overwrite replaces only exact managed outputs,
- unknown files are preserved,
- symlinked output paths and symlinked output ancestry are rejected,
- candidate/eval/raw/benchmark output paths cannot be inside `.local_data`, root, repo root, home directory, or an input parent,
- benchmark suite candidate/eval/output directories cannot dangerously overlap.

### AR-6: No accidental network in tests or CI

Verify and, if needed, harden:

- tests use dry-run or fake clients only,
- tests never depend on a running model server,
- CI smoke workflow does not perform real model calls,
- benchmark suite tests do not call external endpoints,
- tests are deterministic on Linux CI.

## 5. Required tests

Update or add tests under:

```text
tests/eval/test_model_candidate_runner.py
tests/eval/test_benchmark_suite.py
```

Add coverage for any missing cases below:

### Candidate runner tests

- localhost endpoint accepted,
- non-local endpoint rejected without flag,
- endpoint with credentials rejected,
- endpoint with query/fragment rejected,
- API key env value is not serialized in reports/candidates,
- dry-run makes no network calls,
- fake client can generate a valid answer without network,
- arrays/scalars rejected,
- full candidate row rejected,
- surrounding text with one answer object can be parsed,
- `--strict` exits nonzero for parse/validation failure,
- raw output path sanitizes row IDs,
- overwrite preserves unknown files and rejects symlinked managed outputs.

### Benchmark suite tests

- config rejects unsupported top-level/default/model fields,
- model names must be unique and filesystem-safe,
- non-local endpoint requires dual opt-in,
- dry-run suite makes no real network calls,
- rule baseline can run without model calls,
- `--resume`, `--overwrite`, `--skip-candidates`, and `--evaluate-only` mutual exclusions behave correctly,
- resolved config does not include secret values,
- summary JSON/Markdown/CSV are written and parseable,
- output roots reject `.local_data`, symlink ancestry, root/home/repo roots, and dangerous overlaps.

## 6. Docs

Review and update if needed:

```text
docs/eval/model_candidate_runner.md
docs/eval/model_benchmark_suite.md
docs/eval/evaluation_harness.md
README.md
```

Docs must clearly say:

- local endpoint use can still expose private RTL to the local server operator,
- non-local endpoints require explicit opt-in and still require user data-safety review,
- API keys must be supplied through environment variable names only,
- dry-run is the recommended first smoke test,
- evaluator scores are deterministic heuristics, not proof of RTL correctness,
- generated candidates, raw outputs, eval runs, and benchmark outputs should stay local by default.

## 7. Testing plan

Run:

```bash
python -m pytest tests/eval/test_model_candidate_runner.py
python -m pytest tests/eval/test_benchmark_suite.py
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run a network-free dry-run smoke command:

```bash
python scripts/eval/run_benchmark_suite.py \
  --config tests/fixtures/eval/benchmark_suite_dry_run.json \
  --output-dir /tmp/rtl_specializer_benchmark_suite_dry_run \
  --dry-run \
  --json \
  --overwrite
```

Do not run real model endpoints for this audit.

## 8. Definition of done

Done only when:

- Candidate runner is confirmed or hardened against endpoint/secret/output-safety risks.
- Benchmark suite is confirmed or hardened against endpoint/secret/output-safety risks.
- Missing tests from this spec are added.
- Docs clearly warn about local/non-local endpoint data exposure and evaluator limitations.
- CI smoke workflow remains deterministic and does not call model endpoints.
- No generated candidates, raw outputs, benchmark outputs, eval runs, or local raw data are committed.
- No model calls, EDA calls, downloads, training, schema changes, or automatic dataset publication are introduced.

## 9. Codex implementation instructions

Implement this spec exactly.

This is a safety/test audit for existing model-eval tooling. Keep behavior unchanged unless a safety gap or spec mismatch is found. Add targeted tests for any uncovered safety behavior.

After finishing, commit and push. Summarize changed files, commands run, test results, dry-run smoke result, and any remaining risks or tradeoffs.
