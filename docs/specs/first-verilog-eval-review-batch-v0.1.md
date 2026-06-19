# Task Spec: First VerilogEval Review Batch v0.1

## 1. Goal

Use the hardened VerilogEval review-batch workflow to prepare the first local human-review batch from the user's locally staged VerilogEval checkout.

This is not a new model/training feature. This is the first data-operations task that turns local public VerilogEval source material into a small, auditable review workspace for the user.

The intended workflow is:

```text
data/.local_data/verilog-eval-main/
  -> prepare_verilog_eval_review_batch.py
  -> local review workspace with 10 selected draft rows
  -> user manually reviews/edits reviewed_rows.jsonl
  -> later promotion into processed validated rows
```

The most important rule: generated review-batch outputs are a local human-review workspace. They must not be treated as training-ready data and should not be committed unless explicitly approved after license/provenance review.

## 2. Non-goals

Do not build:

- model training,
- model inference,
- LLM calls,
- EDA execution,
- RTL simulation,
- synthesis,
- equivalence,
- automatic downloads,
- automatic promotion,
- automatic license approval,
- schema changes,
- bulk data ingestion beyond the selected review batch.

Do not commit raw VerilogEval files from `data/.local_data/`.

Do not mark generated rows as `validated` or `reviewed`.

## 3. Preconditions

The repo should already include the hardened implementation from:

```text
docs/specs/verilog-eval-review-batch-hardening-v0.1.md
```

The user's local checkout should exist at:

```text
data/.local_data/verilog-eval-main/
```

If this path does not exist, stop and report that local data is missing. Do not download anything.

## 4. Required implementation updates

### FR-1: Make review workspaces local-only by default

Add ignore rules so accidental commits of local review workspaces are avoided.

Update `.gitignore` to ignore generated review workspaces by default:

```text
# Local/generated review workspaces
/data/review/
/data/drafts/
/data/reports/
/data/releases/
/data/eval/candidates/
/data/eval/runs/
```

Keep already tracked source/test files unaffected. Do not delete existing tracked files.

If any of these directories need a README later, add explicit exceptions only when needed. For this task, do not add large generated outputs to Git.

### FR-2: Update docs to explain generated-data tracking policy

Update `data/README.md` and `docs/dataset/verilog_eval_review_workflow.md` to clearly say:

- `data/.local_data/` is local-only raw data.
- `data/review/` is a local-only human review workspace by default.
- `data/drafts/` is untrusted generated draft material by default.
- `data/processed/` may contain promoted/validated rows that can be considered for commit only after human review and license/provenance approval.
- `data/releases/` and `data/eval/runs/` are generated artifacts and should normally stay local unless a specific release artifact is intentionally published.

### FR-3: Run fixture smoke tests before local data

Run:

```bash
python -m pytest tests/dataset/test_verilog_eval_review_batch.py
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Then run at least one fixture smoke command:

```bash
python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input tests/fixtures/verilog_eval_review/local_checkout \
  --output-dir /tmp/rtl_specializer_verilog_eval_directory_review \
  --limit 2 \
  --license "fixture_public_safe" \
  --json \
  --force

python scripts/dataset/validate_dataset.py \
  --input /tmp/rtl_specializer_verilog_eval_directory_review/selected_rows.jsonl \
  --strict
```

If fixture tests fail, stop and fix those issues before using local VerilogEval data.

### FR-4: Generate the first local review batch

If `data/.local_data/verilog-eval-main/` exists, run:

```bash
python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input data/.local_data/verilog-eval-main \
  --output-dir data/review/verilog_eval_batch_001 \
  --limit 10 \
  --license "VerilogEval local public data staged by user; verify exact license/provenance before promotion" \
  --json \
  --force
```

Then validate:

```bash
python scripts/dataset/validate_dataset.py \
  --input data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --strict

python scripts/dataset/validate_dataset.py \
  --input data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --strict
```

The output should remain local and untracked by Git.

### FR-5: Produce a concise local review summary

Write a small local-only summary file under the review workspace:

```text
data/review/verilog_eval_batch_001/NEXT_STEPS.md
```

It should tell the user:

- how many rows were selected,
- where the review Markdown files are,
- where `reviewed_rows.jsonl` is,
- that each answer must be manually reviewed/edited,
- that claim levels must stay conservative without tool evidence,
- that promotion happens later with `promote_reviewed_rows.py`,
- that generated review rows are not training-ready.

This file should be ignored along with the rest of `data/review/`.

### FR-6: Do not commit generated review data

After generating the local review batch, check:

```bash
git status --short
```

Expected tracked changes should be limited to:

```text
.gitignore
data/README.md
docs/dataset/verilog_eval_review_workflow.md
```

and any other small documentation changes needed by this spec.

Generated review workspace files under `data/review/` must be untracked/ignored.

Raw VerilogEval files under `data/.local_data/` must be untracked/ignored.

## 5. Files likely involved

Modify:

```text
.gitignore
data/README.md
docs/dataset/verilog_eval_review_workflow.md
```

Do not modify dataset schema or validators unless a real bug is discovered while running the smoke commands.

Do not commit:

```text
data/.local_data/**
data/review/**
data/drafts/**
data/releases/**
data/eval/runs/**
```

## 6. Acceptance criteria

Done only when:

- Local raw VerilogEval remains ignored and uncommitted.
- Generated review workspace remains ignored and uncommitted.
- Fixture smoke test passes.
- Golden dataset validation passes.
- Local review batch is generated if local data exists.
- `selected_rows.jsonl` and `reviewed_rows.jsonl` validate under `--strict`.
- `NEXT_STEPS.md` exists in the local review workspace.
- Git status does not include raw VerilogEval files or generated review rows.
- No model/LLM/EDA/download/training/schema changes are introduced.

## 7. Expected user follow-up after this task

After this task, the user should manually review:

```text
data/review/verilog_eval_batch_001/review_packet/rows/*.review.md
```

and edit:

```text
data/review/verilog_eval_batch_001/reviewed_rows.jsonl
```

Only after manual review should the user run promotion.

## 8. Codex implementation instructions

Implement this task spec exactly.

This is a local data preparation task, not a training task.

Do not download VerilogEval. Do not call LLMs. Do not run EDA tools. Do not train or run models. Do not promote rows. Do not mark rows validated. Do not commit raw or generated data.

After finishing, commit and push only the tracking-policy/doc changes. Summarize:

- commands run,
- whether local VerilogEval data was found,
- review batch output path,
- number of selected rows,
- validation results,
- git status showing raw/generated data was not committed.
