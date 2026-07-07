# Feature Spec: RTL Answer Teacher Generation Batches v0.1

## 1. Goal

Build a local, manual teacher-answer generation workflow that converts clean `rtl_task_v0.1` task rows into small JSON batches for a human to send to ChatGPT, Claude, or another larger teacher model, then validates returned `rtl_answer_v0.1` answers and merges them with the original tasks into draft chat rows.

This workflow is local preparation only:

```text
clean rtl_task_v0.1 JSONL
  -> deterministic teacher-answer batch export
  -> human manually sends one batch plus prompt template to a teacher model
  -> model returns rtl_answer_v0.1 JSON
  -> local validator checks structure and conservative-claim rules
  -> local merge tool writes draft chat rows
  -> later human review / triage / readiness / promotion
```

The repository must not call any LLM, external API, model endpoint, or download during this workflow.

## 2. Non-goals

Do not build:

- direct ChatGPT, Claude, or model endpoint calls,
- model training or fine-tuning,
- automatic review, readiness approval, or promotion,
- RTL/testbench execution,
- EDA, simulation, synthesis, equivalence, lint, formal, timing, toggle, activity, area, or power analysis,
- dataset schema changes,
- committing generated real teacher batches, returned answers, or draft rows.

## 3. Inputs and outputs

Primary input:

```text
data/review/verilog_eval_rtl_task_v0_1_156.jsonl
```

Each line is a complete `rtl_task_v0.1` object. The tools must preserve task content exactly during export, including prompt, RTL, testbench, provenance, notes, assumptions, `tool_checks`, and embedded candidate/context metadata.

Generated files should stay under ignored local-only directories such as:

```text
data/review/teacher_answer_batches/
data/review/teacher_answer_returns/
data/review/teacher_answer_draft_rows*.jsonl
```

## 4. Export CLI

Add:

```text
scripts/dataset/export_rtl_answer_teacher_batches.py
```

Example:

```bash
python scripts/dataset/export_rtl_answer_teacher_batches.py \
  --input data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --output-dir data/review/teacher_answer_batches \
  --batch-size 5 \
  --force \
  --json
```

Options:

```text
--input <path>         input JSONL of rtl_task_v0.1 rows
--output-dir <path>    output directory for batch_XXX.json files
--batch-size <N>       rows per batch, default 5
--limit <N>            optional cap after sorting/windowing
--start-index <N>      optional zero-based input offset
--force                replace only exact batch files created by this tool
--json                 print machine-readable summary
```

Batch shape:

```json
{
  "batch_schema_version": "rtl_answer_teacher_batch_v0.1",
  "created_by": "export_rtl_answer_teacher_batches",
  "input": "...",
  "batch_index": 1,
  "batch_count": 2,
  "row_count": 5,
  "start_index": 0,
  "prompt_template": "docs/dataset/llm_rtl_answer_generation_prompt.md",
  "rows": []
}
```

The export must be deterministic and must not rewrite row content.

`--force` must replace only managed `batch_XXX.json` files previously created by this tool. It must preserve unknown files and must not write inside `.local_data`.

## 5. Prompt template

Create:

```text
docs/dataset/llm_rtl_answer_generation_prompt.md
```

The template must instruct a human-operated teacher model to:

- return valid JSON only,
- not return Markdown,
- produce one answer per input row,
- produce the same number of answers as input rows,
- return answers in the same order as input rows,
- return a JSON object with an `answers` array,
- preserve `source_id`,
- use only supplied `rtl_task_v0.1` artifacts,
- not invent simulation, lint, synthesis, formal, timing, toggle, area, power, or verification results,
- say checks were not run when `tool_checks` are null,
- avoid inventing a DUT bug for reference-only rows with no candidate DUT source,
- analyze prompt-embedded buggy candidates separately when `design_context.prompt_embedded_candidate_rtl == true` or `artifacts.before_rtl_code` exists,
- treat `design_context.prompt_embedded_context_rtl == true` as context/helper RTL rather than a buggy candidate,
- keep area, power, activity, timing, and synthesis claims as `insufficient_evidence` unless reports are supplied,
- include a verification plan, not verification results.

## 6. Expected answer shape

Returned teacher answers should use this wrapper:

```json
{
  "answers": []
}
```

Each item in `answers` should be a JSON object with this shape:

```json
{
  "schema_version": "rtl_answer_v0.1",
  "source_id": "...",
  "task_type": "...",
  "issue_summary": [],
  "time_reasoning": {},
  "space_reasoning": {},
  "safe_optimization": {},
  "functional_risk": [],
  "verification_plan": [],
  "claim_levels": {
    "correctness": "suggestion_only",
    "area": "insufficient_evidence",
    "activity": "insufficient_evidence",
    "power": "insufficient_evidence"
  },
  "evidence_used": [],
  "limitations": []
}
```

The answer schema is intentionally small and conservative. It is transport validation for manual review, not automatic approval.

## 7. Validator CLI

Add:

```text
scripts/dataset/validate_rtl_answer_teacher_batch.py
```

Example:

```bash
python scripts/dataset/validate_rtl_answer_teacher_batch.py \
  --tasks data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/review/teacher_answer_returns/batch_001_answers.json \
  --output-md data/review/teacher_answer_returns/batch_001_validation.md \
  --output-json data/review/teacher_answer_returns/batch_001_validation.json \
  --strict \
  --json
```

Options:

```text
--tasks <path>         source rtl_task_v0.1 JSONL, or a batch JSON object with rows
--answers <path>       returned rtl_answer_v0.1 JSON, JSON object with rows/answers, JSON array, or JSONL
--output-md <path>     optional deterministic Markdown report
--output-json <path>   optional deterministic JSON report
--strict               fail on warnings as well as errors
--json                 print machine-readable summary
```

Validation checks:

- parseable JSON / JSONL,
- every answer is a JSON object,
- `schema_version == "rtl_answer_v0.1"`,
- `source_id` is present,
- answer `source_id` exists in task input,
- no duplicate answer `source_id`,
- no extra unknown `source_id`,
- required answer fields are present,
- no answer contains a copied full `rtl_task_v0.1`,
- unsupported claim wording is rejected when corresponding tool evidence is absent,
- no simulation/lint/synthesis/power claims are allowed when relevant `tool_checks` are null,
- reference-only rows must not claim a candidate DUT bug unless `prompt_embedded_candidate_rtl` or `artifacts.before_rtl_code` is present,
- prompt-embedded bug rows must not say no candidate DUT source is provided,
- deterministic Markdown and JSON reports are written when requested.

When validating a returned batch against a full task JSONL, the validator treats answer `source_id` values as the intended subset. If the answer file explicitly contains an `expected_source_ids` list, the validator enforces missing-answer checks against that list.

## 8. Merge CLI

Add:

```text
scripts/dataset/merge_rtl_task_answer_rows.py
```

Example:

```bash
python scripts/dataset/merge_rtl_task_answer_rows.py \
  --tasks data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/review/teacher_answer_returns/batch_001_answers.json \
  --output data/review/teacher_answer_draft_rows_batch_001.jsonl \
  --strict \
  --json
```

Options:

```text
--tasks <path>
--answers <path>
--output <path>
--system-prompt <path> optional system prompt text
--strict
--json
```

Output rows are draft chat rows with:

```json
{
  "source_id": "...",
  "created_by": "merge_rtl_task_answer_rows",
  "review_status": "draft",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": {}},
    {"role": "assistant", "content": {}}
  ]
}
```

The merge tool must not mutate the input task or answer files, must not write into `data/golden`, and must not mark rows approved or validated.

## 9. Tests

Use synthetic fixture rows only. Do not commit real VerilogEval data.

Required tests:

- exports deterministic teacher batch files,
- respects `--batch-size`,
- respects `--limit`,
- refuses overwrite without `--force`,
- preserves multiline RTL/testbench text,
- validator accepts a valid conservative answer,
- validator rejects missing `source_id`,
- validator rejects duplicate `source_id`,
- validator rejects unsupported “passed simulation” when `tool_checks.simulation` is null,
- validator rejects reference-only rows that claim a candidate DUT bug,
- validator allows prompt-embedded candidate rows to discuss the prompt bug,
- merge tool creates system/user/assistant message rows in correct order,
- merge tool does not mutate original task files.

## 10. Files likely involved

Create:

```text
docs/specs/rtl-answer-teacher-generation-batches-v0.1.md
docs/dataset/llm_rtl_answer_generation_prompt.md
docs/dataset/rtl_answer_teacher_generation_workflow.md
scripts/dataset/rtl_answer_teacher_batches.py
scripts/dataset/export_rtl_answer_teacher_batches.py
scripts/dataset/validate_rtl_answer_teacher_batch.py
scripts/dataset/merge_rtl_task_answer_rows.py
tests/dataset/test_rtl_answer_teacher_batches.py
```

Modify:

```text
README.md
docs/dataset/verilog_eval_review_workflow.md
```

## 11. Testing plan

Run:

```bash
python -m pytest tests/dataset
python -m pytest tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

## 12. Definition of done

Done only when:

- the spec exists,
- export, prompt, validation, and merge tools are implemented,
- tests cover the required behavior,
- docs include practical commands,
- generated real batch/return/draft outputs remain uncommitted,
- no model calls, downloads, RTL execution, or EDA calls are introduced,
- required tests pass or failures are clearly reported.
