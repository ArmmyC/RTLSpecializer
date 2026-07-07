# RTL answer teacher generation workflow

This workflow prepares clean `rtl_task_v0.1` rows for manual teacher-answer generation, validates returned `rtl_answer_v0.1` JSON, and merges task/answer pairs into draft chat rows.

It does not call ChatGPT, Claude, external APIs, model endpoints, or web downloads. It does not train models, execute RTL/testbenches, run EDA tools, claim tool results, approve rows, or promote rows.

## Flow

```text
clean rtl_task.v0.1 JSONL
  -> export teacher-answer batches
  -> human sends one batch to ChatGPT/Claude/larger teacher model using prompt template
  -> save returned rtl_answer.v0.1 batch locally
  -> validate returned answers
  -> merge tasks + answers into draft chat rows
  -> human review / triage / readiness checks
  -> only later promote approved rows
```

## Step 1: Export teacher-answer batches

Input rows should already be normalized `rtl_task_v0.1` objects, for example:

```text
data/review/verilog_eval_rtl_task_v0_1_156.jsonl
```

Export deterministic local batches:

```bash
python scripts/dataset/export_rtl_answer_teacher_batches.py \
  --input data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --output-dir data/review/teacher_answer_batches \
  --batch-size 5 \
  --force \
  --json
```

This writes files such as:

```text
data/review/teacher_answer_batches/batch_001.json
data/review/teacher_answer_batches/batch_002.json
```

The exporter preserves task content exactly. It does not rewrite prompts, RTL, testbenches, provenance, notes, assumptions, candidate/context metadata, or `tool_checks`.

`--force` only replaces managed `batch_XXX.json` files created by this tool. Unknown files in the output directory are preserved.

## Step 2: Manually ask a teacher model

Open:

- [llm_rtl_answer_generation_prompt.md](llm_rtl_answer_generation_prompt.md)
- one exported teacher batch, such as `batch_001.json`

Paste the prompt template and then the batch JSON into ChatGPT, Claude, or another teacher model manually.

The teacher should return `rtl_answer_v0.1` JSON only. It must not claim simulation, lint, synthesis, formal, timing, toggle, area, activity, or power results unless supplied evidence exists in the task.

Important row types:

- Reference-only rows: do not invent a candidate DUT bug. A conservative answer usually says no candidate DUT-specific bug can be identified from the supplied artifacts.
- `design_context.prompt_embedded_candidate_rtl: true` or non-null `artifacts.before_rtl_code`: analyze the embedded buggy candidate separately from the fixed/reference RTL.
- `design_context.prompt_embedded_context_rtl: true`: treat the embedded module as context/helper RTL, not as a buggy candidate source.

## Step 3: Validate returned answers

Save the returned answer JSON under a local-only path, for example:

```text
data/review/teacher_answer_returns/batch_001_answers.json
```

Validate it:

```bash
python scripts/dataset/validate_rtl_answer_teacher_batch.py \
  --tasks data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/review/teacher_answer_returns/batch_001_answers.json \
  --output-md data/review/teacher_answer_returns/batch_001_validation.md \
  --output-json data/review/teacher_answer_returns/batch_001_validation.json \
  --strict \
  --json
```

The validator checks structure, source IDs, duplicate answers, required answer fields, unsupported claim wording, invented tool-result claims, reference-only candidate-bug claims, and prompt-embedded candidate/context handling.

When validating against the full 156-row task JSONL, the returned answer IDs are treated as the intended subset. If an answer file includes `expected_source_ids`, the validator checks for missing answers against that explicit batch list.

## Step 4: Merge into draft chat rows

After validation passes, merge the original tasks and returned answers into draft chat rows:

```bash
python scripts/dataset/merge_rtl_task_answer_rows.py \
  --tasks data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/review/teacher_answer_returns/batch_001_answers.json \
  --output data/review/teacher_answer_draft_rows_batch_001.jsonl \
  --strict \
  --json
```

Each output line has:

- `messages[0]`: system instruction
- `messages[1]`: original `rtl_task_v0.1`
- `messages[2]`: returned `rtl_answer_v0.1`

The merge output is still draft data. It does not mark rows approved, validated, reviewed, or training-ready.

## Step 5: Human review and later promotion

Run the usual human review, triage, readiness, and promotion workflow on draft rows. Promotion happens only later, after manual approval and readiness checks.

Generated teacher batches, answer returns, validation reports, and draft rows should stay under ignored local-only directories such as `data/review/` unless a maintainer intentionally publishes a reviewed artifact.
