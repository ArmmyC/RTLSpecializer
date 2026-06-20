# Feature Spec: CI Smoke Workflow v0.1

## 1. Goal

Add a minimal GitHub Actions CI workflow that runs the repository's deterministic smoke checks on every pull request and push to `main`.

The repo now has several local dataset/eval CLIs and safety-sensitive path-cleanup behavior. Current review relies on Codex-reported local commands, but no GitHub Actions workflow run is attached to recent commits. A small CI workflow will make future Codex changes easier to verify.

## 2. Non-goals

Do not add:

- model calls,
- local model server startup,
- downloads of datasets,
- EDA tools,
- RTL simulation/synthesis/equivalence/toggle/power analysis,
- training or benchmarking real models,
- artifact upload of generated datasets,
- secrets,
- scheduled jobs,
- deployment.

Do not commit generated outputs from CI.

## 3. Workflow file

Create:

```text
.github/workflows/ci-smoke.yml
```

Workflow name:

```text
CI Smoke
```

Triggers:

```yaml
on:
  pull_request:
  push:
    branches: [main]
```

Use a single Linux job on `ubuntu-latest`.

Use Python 3.11 by default.

## 4. Required checks

The workflow must run deterministic local checks only:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python -m pytest tests/dataset tests/eval
```

If the full `tests/dataset tests/eval` suite becomes too slow later, split into separate jobs in a future spec. Do not prematurely optimize in this v0.1.

## 5. Dependency behavior

The current project should use only Python standard library for these checks unless the repo later adds a dependency file.

CI should:

- checkout the repo,
- set up Python 3.11,
- print `python --version`,
- run validation,
- run pytest.

If `pytest` is not available in the runner image, install only `pytest` in the workflow:

```bash
python -m pip install pytest
```

Do not add broad dependency installation unless a repo dependency file exists and is needed by tests.

## 6. Safety requirements

The workflow must not:

- access secrets,
- call network endpoints except GitHub checkout/setup-python and Python package installation for pytest,
- call OpenAI or local model endpoints,
- run model candidate generation against a real endpoint,
- run benchmark suite against real models,
- download raw datasets,
- write under tracked data directories except temporary test paths,
- upload artifacts containing dataset rows, RTL, or model outputs.

Tests may use temporary directories and checked-in synthetic fixtures.

## 7. Docs

Update:

```text
README.md
```

Add a short CI section explaining:

- CI runs deterministic dataset/eval smoke checks,
- CI does not run model calls, EDA, simulation, synthesis, training, or downloads,
- local generated data remains ignored and should not be committed.

Optionally update:

```text
AGENTS.md
```

Mention that Codex should include CI status or note that no CI status is available when reporting changes.

## 8. Tests / validation after adding CI

Because the workflow itself cannot run until pushed, run locally or via Codex before committing:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python -m pytest tests/dataset tests/eval
```

Also inspect the workflow syntax manually for valid YAML indentation.

## 9. Definition of done

Done only when:

- `.github/workflows/ci-smoke.yml` exists.
- CI runs on pull requests and pushes to `main`.
- CI validates the golden dataset strictly.
- CI runs dataset and eval tests.
- CI avoids model calls, EDA, downloads of raw datasets, training, and artifact uploads.
- README documents the CI smoke behavior.
- Existing local tests listed above pass or any failure is clearly reported.

## 10. Codex implementation instructions

Implement this spec exactly.

Keep the workflow minimal and deterministic. Do not add new source-code features while adding CI.

After finishing, commit and push. Summarize changed files, commands run, local test results, and whether GitHub Actions reported a workflow status after push.
