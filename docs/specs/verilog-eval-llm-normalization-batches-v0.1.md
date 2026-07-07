# Feature Spec: VerilogEval LLM Normalization Batches v0.1

## 1. Goal

Build the first local batching workflow that prepares raw VerilogEval task inputs for manual submission to ChatGPT or Claude so those tools can normalize the tasks into `rtl_task_v0.1`.

This feature is strictly a local export-and-validate workflow:

```text
local VerilogEval source rows
  -> deterministic JSON batch export
  -> human manually sends one batch plus prompt template to ChatGPT/Claude
  -> model returns normalized rtl_task_v0.1 JSON
  -> local validator checks preservation and no-invention rules
  -> later workflow can draft rtl_answer_v0.1 and route through review/readiness/promotion
```

The repo must not call any LLM, external API, model endpoint, or download during this workflow.

## 2. Non-goals

Do not build:

- direct ChatGPT or Claude calls,
- model endpoint integration,
- model training or fine-tuning,
- automatic review or promotion,
- `rtl_answer_v0.1` generation,
- RTL/testbench execution,
- EDA, simulation, synthesis, equivalence, toggle, or power analysis,
- dataset schema changes,
- committing generated batch JSON.

Do not rewrite the existing import/review/finalization pipeline beyond the small helper changes needed to preserve exact source prompt text for this export workflow.

## 3. User stories

- As a dataset builder, I want to export small deterministic raw-task batches so I can paste them into ChatGPT or Claude manually.
- As a reviewer, I want every exported row to preserve the exact prompt/spec, RTL, and testbench text so signal names and reset wording cannot drift.
- As a maintainer, I want a local validator for returned normalized task batches so invented tool evidence or accidental answer content is caught before later workflow steps.
- As a user, I want `--force` to replace only this tool's own batch files and preserve any unrelated notes in the output directory.

## 4. CLI UX

Add:

```text
scripts/dataset/export_verilog_eval_normalization_batches.py
```

Example:

```bash
python scripts/dataset/export_verilog_eval_normalization_batches.py \
  --input data/.local_data/verilog-eval-main \
  --output-dir data/review/verilog_eval_normalization_batches \
  --batch-size 10 \
  --json
```

Supported options:

```text
--input <path>         local VerilogEval source input
--output-dir <path>    output directory for batch_XXX.json files
--batch-size <N>       rows per batch, default 10
--limit <N>            optional cap after sorting and start-index
--start-index <N>      optional zero-based offset after sorting
--force                replace only exact batch files created by this tool
--json                 print machine-readable summary
```

Add a local validator CLI:

```text
scripts/dataset/validate_verilog_eval_normalized_batch.py
```

Example:

```bash
python scripts/dataset/validate_verilog_eval_normalized_batch.py \
  --raw-batch data/review/verilog_eval_normalization_batches/batch_001.json \
  --normalized returned_batch_001.json \
  --json
```

Supported options:

```text
--raw-batch <path>     exported raw batch JSON from this repo
--normalized <path>    returned normalized JSON from manual LLM conversion
--json                 print machine-readable summary
```

Exit behavior:

- Export CLI exits `0` when batch files are written successfully, even if some source rows were rejected, as long as at least one row was exported.
- Export CLI exits `1` for invalid arguments, unsafe overwrite requests, unreadable inputs, or zero exported rows.
- Validator exits `0` only when the returned normalized batch is parseable and passes all checks.
- Validator exits `1` for malformed JSON, row mismatches, invented evidence, missing source rows, or task-shape violations.

## 5. Functional requirements

### FR-1: Reuse existing VerilogEval discovery helpers

The exporter must reuse the existing local VerilogEval discovery logic where practical.

Supported local inputs should match the current VerilogEval import workflow:

- a local VerilogEval checkout with `dataset_spec-to-rtl/*_prompt.txt`, `*_ref.sv`, and optional `*_test.sv`,
- a conservative VerilogEval-style JSONL export,
- a local manifest JSONL shaped for the existing public import flow.

Do not download any dataset. Read only local files.

### FR-2: Preserve exact source prompt/spec text

The existing review importer uses `artifacts.lint_log` as a prompt/spec carrier for VerilogEval review context. That behavior should remain unchanged for current review workflows.

For this new batching workflow, preserve the exact source prompt/spec text separately as raw prompt text without labeling it as tool evidence.

Implementation requirement:

- surface exact prompt/spec text from supported inputs,
- do not prefix, summarize, or rewrite it,
- do not move it into a fake lint log in the exported raw batch.

If a small shared helper or metadata extension is needed so the VerilogEval adapter exposes raw prompt text, do that in a backward-compatible way.

### FR-3: Deterministic batch export

Write deterministic JSON batch files such as:

```text
data/review/verilog_eval_normalization_batches/batch_001.json
data/review/verilog_eval_normalization_batches/batch_002.json
```

Determinism requirements:

- sort discovered source rows deterministically before slicing,
- apply `--start-index` and then `--limit`,
- chunk rows deterministically by `--batch-size`,
- use stable JSON formatting and newline termination.

Each batch file must be parseable JSON and contain enough metadata to understand the batch without external state.

Suggested file shape:

```json
{
  "batch_schema_version": "verilog_eval_llm_normalization_batch_v0.1",
  "created_by": "export_verilog_eval_normalization_batches",
  "input": "...",
  "batch_index": 1,
  "batch_count": 2,
  "row_count": 10,
  "prompt_template": "docs/dataset/llm_rtl_task_normalization_prompt.md",
  "rows": [ ... ]
}
```

### FR-4: Export raw rows only

Each exported row must preserve exact source text and include fields like:

- `source_id`,
- `source_dataset`,
- `license`,
- `provenance`,
- `design_family`,
- `task_type`,
- `user_goal`,
- `raw_prompt`,
- `raw_reference_rtl`,
- `raw_testbench`,
- `tool_checks`,
- `notes`.

Rules:

- `raw_prompt`, `raw_reference_rtl`, and `raw_testbench` must preserve exact text when present.
- `raw_testbench` may be `null` when absent upstream.
- `tool_checks` must default to known tool keys with `null` values unless real local source data explicitly provided those checks.
- no assistant-answer content may appear in exported raw rows.
- no normalization or summarization may be applied to the source text.

### FR-5: Safe overwrite behavior

`--force` must replace only exact batch files previously created by this tool inside the exact output directory.

Requirements:

- Without `--force`, fail if any target batch file already exists.
- With `--force`, remove only files that can be positively identified as this tool's managed batch outputs.
- Preserve unknown files in the output directory such as `notes.md`, `README.txt`, or unrelated JSON.
- Do not delete parent directories.
- Do not delete or modify anything inside `.local_data`.
- Reject output directories inside `.local_data`.

It is acceptable for `--force` to replace stale `batch_XXX.json` files from a previous run when those files are clearly identified as managed outputs from this tool.

### FR-6: Prompt template for manual LLM normalization

Create:

```text
docs/dataset/llm_rtl_task_normalization_prompt.md
```

The prompt template must instruct ChatGPT/Claude to convert each raw row into normalized `rtl_task_v0.1` task JSON while:

- preserving exact prompt/spec text in a `prompt` field,
- preserving exact RTL/testbench text in task artifacts,
- not inventing logs, reports, or tool results,
- setting missing tool evidence fields to `null`,
- keeping provenance, license, and source identifiers,
- returning valid JSON only,
- not producing any `rtl_answer_v0.1` content,
- not dropping rows from the batch.

Because this is a normalization transport format rather than a complete dataset row, the returned normalized row may carry `rtl_task_v0.1` fields plus traceability metadata such as `source_id`, `source_dataset`, `license`, `provenance`, and `design_family`.

### FR-7: Returned normalized batch validation

Add a local validation helper and CLI for returned normalized batches.

Validation requirements:

- the normalized file must parse as either a JSON array of rows or a JSON object with a `rows` array,
- every row must be a JSON object,
- every row must keep `source_id`,
- every row must have `schema_version == "rtl_task_v0.1"`,
- no row may contain `rtl_answer_v0.1` content or assistant-answer sections such as `issue_summary`, `time_reasoning`, `space_reasoning`, `safe_optimization`, `functional_risk`, `verification_plan`, `claim_levels`, or `patch`,
- prompt/spec text must be non-empty and must exactly match the exported `raw_prompt`,
- `artifacts.rtl_code` must exactly match `raw_reference_rtl`,
- `artifacts.testbench` must exactly match `raw_testbench` when present, or be clearly `null`/empty when absent,
- `artifacts.lint_log`, `artifacts.synthesis_report`, and `artifacts.toggle_report` must remain `null` unless the raw batch explicitly carried real tool text for those artifacts,
- row counts and `source_id` membership must match the raw batch.

The validator should return clear JSON/text errors. It must not rewrite either input file.

### FR-8: Keep current dataset schema unchanged

Do not change `dataset_v0.1`, `rtl_task_v0.1`, or `rtl_answer_v0.1`.

If the returned normalized task objects need a `prompt` field for exact source preservation, treat that as transport metadata tolerated by local tools, not as a schema migration.

### FR-9: Tests

Add tests using small synthetic fixture rows only.

Required export tests:

- exports deterministic batch files,
- respects `--batch-size`,
- respects `--limit`,
- respects `--start-index`,
- refuses overwrite without `--force`,
- preserves multiline RTL/testbench text,
- writes parseable JSON,
- generated rows contain provenance and `source_id`,
- `--force` preserves unknown files in the output directory,
- prompt template exists and contains the no-invention and null-tool-evidence rules.

Required validator tests:

- accepts a valid normalized batch,
- rejects missing `source_id`,
- rejects wrong `schema_version`,
- rejects accidental answer fields,
- rejects prompt/RTL/testbench drift,
- rejects invented tool evidence in task artifacts,
- validator CLI JSON output is parseable.

Do not add real VerilogEval data.

## 6. Architecture requirements

Prefer a reusable module:

```text
scripts/dataset/verilog_eval_normalization_batches.py
```

and thin CLIs:

```text
scripts/dataset/export_verilog_eval_normalization_batches.py
scripts/dataset/validate_verilog_eval_normalized_batch.py
```

Suggested functions:

```python
def export_verilog_eval_normalization_batches(...) -> tuple[dict[str, object], int]:
    ...

def validate_verilog_eval_normalized_batch(raw_batch_path: Path, normalized_path: Path) -> tuple[dict[str, object], int]:
    ...
```

Use only the Python standard library and existing project helpers unless a current repo helper is clearly a better fit.

## 7. Security and safety

Treat all prompt/RTL/testbench content as untrusted data.

Do not execute:

- RTL,
- testbenches,
- generated code,
- shell commands embedded in task text,
- Python snippets embedded in artifacts.

Do not call models or external endpoints.

Keep generated batch files local-only under ignored directories such as `data/review/`.

## 8. Files likely involved

Create:

```text
docs/specs/verilog-eval-llm-normalization-batches-v0.1.md
docs/dataset/llm_rtl_task_normalization_prompt.md
docs/dataset/llm_normalization_batch_workflow.md
scripts/dataset/verilog_eval_normalization_batches.py
scripts/dataset/export_verilog_eval_normalization_batches.py
scripts/dataset/validate_verilog_eval_normalized_batch.py
tests/dataset/test_verilog_eval_normalization_batches.py
```

Modify:

```text
README.md
docs/dataset/verilog_eval_review_workflow.md
scripts/dataset/adapters/verilog_eval.py
scripts/dataset/adapters/manifest.py
```

Only change other files when needed for shared helper reuse or test support.

## 9. Testing plan

Run:

```bash
python -m pytest tests/dataset
python -m pytest tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

## 10. Definition of done

Done only when:

- the new spec exists and the implementation matches it,
- deterministic raw batch export works for supported local VerilogEval inputs,
- manual prompt-template workflow is documented,
- returned normalized tasks can be validated locally for preservation and no-invention rules,
- tests cover export and validation behavior,
- no LLMs, model endpoints, downloads, RTL execution, or EDA calls are introduced,
- generated batch JSON is not committed.
