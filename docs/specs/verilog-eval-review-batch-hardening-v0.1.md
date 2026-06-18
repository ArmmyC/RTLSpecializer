# Fix Spec: VerilogEval Review Batch Hardening v0.1

## 1. Goal

Harden the VerilogEval review-batch implementation before using it on the real locally staged VerilogEval checkout.

The previous implementation is broadly on-spec: it adds a local review-batch CLI, supports manifest/local directory/local JSONL discovery, generates conservative draft rows, selects review rows, creates review packets, writes a `reviewed_rows.jsonl` editing template, and documents synthetic seed-row provenance.

This fix spec targets the remaining safety, UX, and edge-case gaps that matter before real data review begins:

- prevent accidental overwrite of existing review-batch outputs,
- reject duplicate generated row IDs deterministically,
- validate selected/draft outputs as full JSONL groups, not only row-by-row,
- test the local VerilogEval directory and JSONL paths, not only manifest input,
- make prompt/spec display in review packets clearer and less misleading,
- improve CLI errors for invalid source/license/input cases,
- keep raw VerilogEval data ignored and uncommitted.

Do not add model training, model inference, LLM calls, EDA execution, RTL execution, downloads, or schema changes.

## 2. Current implementation summary

Implemented files include:

```text
scripts/dataset/prepare_verilog_eval_review_batch.py
scripts/dataset/adapters/verilog_eval.py
docs/dataset/verilog_eval_review_workflow.md
tests/dataset/test_verilog_eval_review_batch.py
tests/fixtures/verilog_eval_review/manifest.jsonl
```

The current implementation is good enough structurally but should be hardened before processing the user's real local data at:

```text
data/.local_data/verilog-eval-main/
```

## 3. Non-goals

Do not build:

- model training,
- model inference,
- external LLM calls,
- automatic dataset download,
- web scraping,
- RTL simulation,
- synthesis,
- equivalence,
- toggle/power analysis,
- schema version changes,
- automatic promotion to `validated`,
- automatic license approval,
- committing raw VerilogEval files.

## 4. Required fixes

### FR-1: Output directory safety

The CLI currently writes into `--output-dir` and may overwrite previous review-batch artifacts.

Implement safe behavior:

- If `--output-dir` exists and is non-empty, fail with a clear error by default.
- Add one explicit override flag:

```text
--force
```

- `--force` may delete/replace only files created by this tool under the exact output directory.
- Never delete parent directories.
- Never delete `data/.local_data/`.
- Never delete raw VerilogEval source directories.

Add tests:

- existing non-empty output dir fails without `--force`,
- existing non-empty output dir succeeds with `--force`,
- `--force` does not touch files outside the output dir.

### FR-2: Full-file validation after row generation

The current code validates each generated row individually, then writes `draft_rows.jsonl` and `selected_rows.jsonl`. Individual validation can miss full-file issues such as duplicate row IDs.

Add full-file validation for:

```text
draft_rows.jsonl
selected_rows.jsonl
reviewed_rows.jsonl
```

Validation requirements:

- individual invalid rows should be rejected as today,
- duplicate row IDs must be detected before output is considered OK,
- selected rows must validate as a group,
- reviewed template rows must validate as a group,
- if group validation fails, return `ok: false` and non-zero exit code.

Add tests for duplicate source IDs producing duplicate row IDs.

### FR-3: Deterministic duplicate ID handling

If two VerilogEval examples produce the same output row ID, reject later duplicates deterministically.

Rules:

- Keep the first row by sorted discovery order.
- Reject subsequent duplicates with reason:

```text
duplicate output row id
```

- Include `source_id`, duplicate row ID, and source metadata in the rejected row.
- Do not silently overwrite.

### FR-4: Validate source early

The CLI accepts `--source`, defaulting to `public_verilog_eval`.

Add early validation:

- `--source` must be one of the known dataset source constants.
- For this tool, strongly prefer `public_verilog_eval`; other public sources may be rejected unless there is a clear reason.
- If invalid, fail before discovery with a clear error listing allowed values.

Add tests for invalid source.

### FR-5: Better prompt/spec handling in review packets

The VerilogEval adapter currently stores the prompt/spec in `lint_log` with the prefix:

```text
VerilogEval prompt/specification for reviewer context:
```

This is acceptable as a schema-preserving workaround, but review display should not make it look like an actual lint report or SystemVerilog artifact.

Improve review-packet rendering:

- If artifact field is `lint_log` and the value starts with `VerilogEval prompt/specification`, render it under a heading like:

```text
### VerilogEval prompt/specification
```

- Use a plain text fenced block for report/prompt fields, not `systemverilog`.
- Keep RTL fields as `systemverilog` fenced blocks.
- Make review Markdown clearly distinguish:
  - task prompt/spec,
  - reference RTL,
  - testbench/checker,
  - draft assistant answer.

Do not change dataset schema for this fix.

Add a test that a generated review Markdown file contains `VerilogEval prompt/specification` and does not label that prompt block as SystemVerilog.

### FR-6: Add local directory fixture coverage

The existing tests cover manifest input. Add a tiny synthetic directory fixture shaped like VerilogEval:

```text
tests/fixtures/verilog_eval_review/local_checkout/dataset_spec-to-rtl/
  Prob001_counter_prompt.txt
  Prob001_counter_ref.sv
  Prob001_counter_test.sv
  Prob002_shift_prompt.txt
  Prob002_shift_ref.sv
```

Test that:

- `prepare_verilog_eval_review_batch.py --input tests/fixtures/verilog_eval_review/local_checkout ...` succeeds,
- selected rows are valid draft rows,
- prompt/spec content is included in the review packet,
- no raw external VerilogEval content is added to fixtures.

### FR-7: Add local JSONL fixture coverage

Add a tiny JSONL fixture:

```text
tests/fixtures/verilog_eval_review/verilog_eval_export.jsonl
```

Use synthetic rows with alias fields such as:

```json
{"task_id":"json_counter","prompt":"...","canonical_solution":"module ... endmodule","testbench":"module tb; endmodule"}
```

Test that:

- JSONL input succeeds,
- missing prompt or missing module RTL is rejected clearly,
- `--limit` is respected.

### FR-8: Improve selection report

`selection_report.json` should include enough information for audit.

Add fields:

```json
{
  "input": "...",
  "source": "public_verilog_eval",
  "license": "...",
  "discovered_rows": 0,
  "valid_draft_rows": 0,
  "selected_rows": 0,
  "rejected_rows": 0,
  "selection": [
    {
      "id": "...",
      "source_id": "...",
      "design_family": "...",
      "task_family": "...",
      "selection_score": 0,
      "score_reasons": ["counter", "testbench", "clock/reset"]
    }
  ],
  "rejected": [],
  "warnings": [],
  "errors": []
}
```

The exact field names can vary, but the report must show why rows were selected.

### FR-9: Preserve raw-data ignore rules

Ensure `.gitignore` includes:

```text
.local_data/
data/.local_data/
```

Add or keep docs warning:

```text
Raw VerilogEval files must stay local-only and must not be committed.
```

Do not commit `data/.local_data/` contents.

### FR-10: UX improvements

Improve CLI text/JSON result:

- Include `input`, `source`, and `license` in result JSON.
- Include `rejected_rows_path` in result JSON.
- Print a warning when `reviewed_rows.jsonl` is only a draft editing template.
- On failure, print actionable next steps.

## 5. Files likely involved

Modify:

```text
scripts/dataset/prepare_verilog_eval_review_batch.py
scripts/dataset/adapters/verilog_eval.py
scripts/dataset/prepare_review_packet.py
tests/dataset/test_verilog_eval_review_batch.py
docs/dataset/verilog_eval_review_workflow.md
docs/dataset/dataset_guidelines.md
README.md
.gitignore
```

Add fixtures:

```text
tests/fixtures/verilog_eval_review/local_checkout/dataset_spec-to-rtl/*
tests/fixtures/verilog_eval_review/verilog_eval_export.jsonl
```

Do not modify unrelated files.

## 6. Testing plan

Run:

```bash
python -m pytest tests/dataset/test_verilog_eval_review_batch.py
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Run fixture smoke commands:

```bash
python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input tests/fixtures/verilog_eval_review/manifest.jsonl \
  --output-dir /tmp/rtl_specializer_verilog_eval_manifest_review \
  --limit 3 \
  --license "fixture_public_safe" \
  --json \
  --force

python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input tests/fixtures/verilog_eval_review/local_checkout \
  --output-dir /tmp/rtl_specializer_verilog_eval_directory_review \
  --limit 2 \
  --license "fixture_public_safe" \
  --json \
  --force

python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input tests/fixtures/verilog_eval_review/verilog_eval_export.jsonl \
  --output-dir /tmp/rtl_specializer_verilog_eval_jsonl_review \
  --limit 2 \
  --license "fixture_public_safe" \
  --json \
  --force
```

Then validate:

```bash
python scripts/dataset/validate_dataset.py \
  --input /tmp/rtl_specializer_verilog_eval_manifest_review/selected_rows.jsonl \
  --strict

python scripts/dataset/validate_dataset.py \
  --input /tmp/rtl_specializer_verilog_eval_directory_review/selected_rows.jsonl \
  --strict

python scripts/dataset/validate_dataset.py \
  --input /tmp/rtl_specializer_verilog_eval_jsonl_review/selected_rows.jsonl \
  --strict
```

If the user's local data exists, also run:

```bash
python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input data/.local_data/verilog-eval-main \
  --output-dir /tmp/rtl_specializer_verilog_eval_local_review \
  --limit 10 \
  --license "VerilogEval local public data staged by user; verify exact license/provenance before promotion" \
  --json \
  --force

python scripts/dataset/validate_dataset.py \
  --input /tmp/rtl_specializer_verilog_eval_local_review/selected_rows.jsonl \
  --strict
```

Do not fail the full test suite if the user's local data path is absent; only the synthetic fixtures are required in CI.

## 7. Definition of done

Done only when:

- Existing output directories are protected by default.
- `--force` is explicit and scoped to the exact output directory.
- Duplicate generated row IDs are rejected deterministically.
- Draft, selected, and reviewed-template outputs validate as complete JSONL files.
- Manifest, local directory, and local JSONL input paths are tested.
- Prompt/spec display is clear and not mislabeled as SystemVerilog.
- Selection report explains selected rows.
- `.gitignore` protects local raw data paths.
- No raw VerilogEval dataset content is committed.
- No model calls, EDA calls, downloads, training, or schema changes are introduced.

## 8. Codex implementation instructions

Implement this fix spec exactly.

Keep the existing VerilogEval review-batch feature, but harden it for real local use before the user starts manual review.

After finishing, commit and push. Summarize changed files, test commands, smoke commands, results, and any tradeoffs.
