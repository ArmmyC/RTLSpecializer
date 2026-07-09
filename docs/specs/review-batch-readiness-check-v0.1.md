# Feature Spec: Review Batch Readiness Check v0.1

## 1. Goal

Add a local, deterministic pre-promotion checker for human-reviewed draft batches.

The repo can now prepare a local VerilogEval review workspace with `selected_rows.jsonl`, review Markdown files, and an editable `reviewed_rows.jsonl`. The next highest-value step is to help the human reviewer see whether the edited `reviewed_rows.jsonl` is ready for promotion before running `promote_reviewed_rows.py`.

This feature adds a read-only readiness checker:

```text
selected_rows.jsonl + reviewed_rows.jsonl
  -> validate both files
  -> compare IDs and answers
  -> detect unchanged/stub answers
  -> run public promotion gates in dry-run mode
  -> write readiness report
  -> tell reviewer which rows still need work
```

The checker must not promote rows, modify reviewed rows, call models, run EDA tools, execute RTL, download data, or mark anything validated.

## 2. Non-goals

Do not build:

- model training,
- model inference,
- LLM-based review,
- automatic answer rewriting,
- automatic promotion,
- license approval automation,
- EDA execution,
- RTL simulation/synthesis/equivalence/toggle/power analysis,
- schema version changes,
- web UI.

Do not commit generated review-batch data.

## 3. User stories

- As a human reviewer, I want to know which rows are still unedited stubs before promotion.
- As a dataset maintainer, I want missing/extra/duplicate row IDs caught before promotion.
- As a reviewer, I want a Markdown checklist report that tells me what to fix row by row.
- As a project lead, I want promotion readiness to use the same gates as promotion, without writing processed outputs.
- As a future trainer, I want only genuinely reviewed rows to pass into `data/processed/`.

## 4. CLI UX

Add:

```text
scripts/dataset/check_review_batch_readiness.py
```

Example:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --json
```

Supported options:

```text
--selected <path>       selected draft rows from batch preparation
--reviewed <path>       human-edited reviewed_rows.jsonl
--output-json <path>    optional JSON report path
--output-md <path>      optional Markdown report path
--strict                fail on warnings, missing rows, extra rows, or any not-ready row
--json                  print JSON summary
```

Text success example:

```text
Review readiness check completed.

Selected rows: 10
Reviewed rows: 10
Ready rows: 8
Needs work: 2
Missing rows: 0
Extra rows: 0
Output JSON: data/review/verilog_eval_batch_001/readiness_report.json
Output Markdown: data/review/verilog_eval_batch_001/readiness_report.md
```

Exit-code behavior:

- Exit `0` if the command ran and at least one row is checkable.
- Exit `1` for malformed input files, duplicate IDs, or invalid CLI arguments.
- With `--strict`, exit `1` if any row is not ready, or if missing/extra rows exist.

## 5. Functional requirements

### FR-1: Load and validate both files

Load both JSONL files using existing helpers.

Validate each file with existing strict dataset validation:

```text
validate_dataset_file(..., strict=True)
```

Report validation errors separately for `selected` and `reviewed`.

### FR-2: Compare row IDs

Compute:

```text
selected_ids
reviewed_ids
missing_reviewed_rows = selected_ids - reviewed_ids
extra_reviewed_rows = reviewed_ids - selected_ids
```

Duplicate IDs in either file are errors.

The checker should continue reporting as much as possible if there are missing/extra rows, but `--strict` must fail.

### FR-3: Detect unchanged rows

For each matched row:

- compare the assistant answer in the selected row vs reviewed row,
- compare full row if helpful,
- mark row as unchanged if the assistant answer is identical,
- mark row as possibly edited if answer changed.

Unchanged rows should usually be `needs_work`, because selected rows are imported draft stubs.

### FR-4: Reuse promotion gates without writing outputs

For each matched reviewed row, reuse existing promotion-readiness logic from:

```text
scripts.dataset.review_promotion.public_promotion_errors
scripts.dataset.review_promotion.is_stub_answer
```

Do not call `promote_rows` because it writes processed outputs.

Per-row readiness should include:

```json
{
  "id": "...",
  "ready": false,
  "changed_from_selected": false,
  "is_stub_answer": true,
  "promotion_errors": ["stub answer must be edited before promotion"],
  "validation_errors": [],
  "warnings": [],
  "suggested_next_action": "Edit assistant answer with concrete signal-grounded reasoning."
}
```

A row is ready only if:

- it exists in both selected and reviewed files,
- it validates structurally,
- its assistant answer changed from the selected draft answer,
- `is_stub_answer(answer)` is false,
- `public_promotion_errors(row, allow_stub_answer=False)` returns no errors.

### FR-5: Claim-safety and evidence summary

For each row, summarize claim levels:

```json
"claim_levels": {
  "correctness": "suggestion_only",
  "area": "insufficient_evidence",
  "activity": "insufficient_evidence",
  "power": "insufficient_evidence"
}
```

Flag risky patterns:

- `correctness: verified` without tool evidence,
- `area`, `activity`, or `power` stronger than `insufficient_evidence` without relevant reports,
- power claim without power report,
- missing lint/compile in verification plan.

Use existing validator/promotion helpers where possible; do not duplicate large validation logic.

### FR-6: Write JSON and Markdown reports

If `--output-json` is provided, write a JSON report:

```json
{
  "ok": true,
  "selected_rows": 10,
  "reviewed_rows": 10,
  "matched_rows": 10,
  "ready_rows": 8,
  "needs_work_rows": 2,
  "missing_reviewed_rows": [],
  "extra_reviewed_rows": [],
  "errors": [],
  "warnings": [],
  "rows": []
}
```

If `--output-md` is provided, write a Markdown report with:

- summary counts,
- rows ready table,
- rows needing work table,
- missing/extra rows,
- common promotion errors,
- next commands.

Markdown should tell the user:

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output data/processed/verilog_eval_validated_v0.1.jsonl \
  --report data/reports/verilog_eval_validated_v0.1_report.json \
  --json
```

but only after all rows are ready.

### FR-7: Keep generated readiness outputs local-only

`data/review/` is ignored, so readiness reports written there are local-only by default.

Do not add generated readiness report files to Git.

### FR-8: Add tests

Add tests under:

```text
tests/dataset/test_review_batch_readiness.py
```

Required tests:

- identical selected/reviewed rows are marked not ready,
- edited non-stub row can be marked ready,
- missing reviewed row is reported,
- extra reviewed row is reported,
- duplicate reviewed row IDs fail,
- invalid reviewed row is reported,
- JSON report is written and parseable,
- Markdown report is written and includes next-action guidance,
- `--strict` exits non-zero when any row needs work,
- CLI `--json` output is parseable.

Use small synthetic fixture rows derived from existing public review fixtures. Do not add real VerilogEval content.

### FR-9: Add docs

Create or update:

```text
docs/dataset/review_readiness_workflow.md
```

Update:

```text
docs/dataset/verilog_eval_review_workflow.md
README.md
```

Docs must explain:

- where the readiness check fits after manual review and before promotion,
- why unchanged imported stubs should not be promoted,
- how to read the readiness report,
- how to rerun after editing,
- how to promote only after all intended rows are ready.

## 6. Architecture requirements

Prefer creating a reusable module:

```text
scripts/dataset/review_readiness.py
```

and a thin CLI:

```text
scripts/dataset/check_review_batch_readiness.py
```

Suggested functions:

```python
def load_review_files(selected_path: Path, reviewed_path: Path) -> LoadResult:
    ...

def check_review_readiness(selected_rows: list[dict], reviewed_rows: list[dict]) -> ReadinessResult:
    ...

def write_readiness_reports(result: ReadinessResult, output_json: Path | None, output_md: Path | None) -> None:
    ...
```

Use dataclasses where helpful.

Use only standard library and existing project helpers.

## 7. Security and safety

Treat dataset content as untrusted data.

Do not execute RTL, testbenches, shell commands, report content, or generated text.

Do not call external services.

Do not download anything.

Do not import or execute generated Python from dataset content.

## 8. Files likely involved

Create:

```text
scripts/dataset/review_readiness.py
scripts/dataset/check_review_batch_readiness.py
tests/dataset/test_review_batch_readiness.py
docs/dataset/review_readiness_workflow.md
```

Modify:

```text
docs/dataset/verilog_eval_review_workflow.md
README.md
```

Do not modify schemas unless a real bug is discovered and documented separately.

## 9. Testing plan

Run:

```bash
python -m pytest tests/dataset/test_review_batch_readiness.py
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Run a fixture smoke command using an existing generated review fixture or temporary files from tests:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected tests/fixtures/public_review/draft_rows.jsonl \
  --reviewed tests/fixtures/public_review/reviewed_rows.jsonl \
  --output-json /tmp/rtl_specializer_review_readiness.json \
  --output-md /tmp/rtl_specializer_review_readiness.md \
  --json
```

If the user's local review batch exists, also run:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --json
```

Do not fail CI if the local review batch is absent.

## 10. Definition of done

Done only when:

- Readiness checker CLI exists.
- Reusable readiness module exists.
- Matched/missing/extra/duplicate rows are reported clearly.
- Unchanged draft-stub rows are marked not ready.
- Promotion gates are reused without writing processed outputs.
- JSON and Markdown reports are produced.
- Tests cover pass/fail/missing/extra/duplicate/invalid/strict/CLI cases.
- Docs explain manual review -> readiness check -> promotion.
- No rows are promoted or marked validated by this feature.
- No model calls, EDA calls, downloads, training, or schema changes are introduced.

## 11. Codex implementation instructions

Implement this spec exactly.

This feature helps the human reviewer decide whether edited rows are ready for promotion. It must not perform the review automatically and must not promote rows.

After finishing, commit and push. Summarize changed files, commands run, test results, and any tradeoffs.
