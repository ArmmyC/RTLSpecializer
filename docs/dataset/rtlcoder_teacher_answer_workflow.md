# RTLCoder teacher-answer workflow

This workflow starts from synthetic RTLCoder bug draft tasks, exports deterministic teacher-answer batches, validates returned `rtl_answer_v0.1` JSON, and merges task/answer pairs into teacher-distill-style draft rows.

It does not call external APIs or LLMs from the repository, does not train, does not run RTL or EDA tools, does not mark rows human-reviewed, and does not promote anything to golden.

## Input

Start from:

```text
data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl
```

Each row is expected to remain:

- `schema_version: rtl_task_v0.1`
- `synthetic_bug: true`
- `artifacts.rtl_code = original/reference RTL`
- `artifacts.before_rtl_code = synthetic buggy candidate RTL`
- `review_status: synthetic_draft`
- `approval_status: not_approved`
- `promotion_allowed: false`

## Step 1: Export teacher-answer batches

Use the RTLCoder wrapper if you want the RTLCoder default batch size of 10:

```bash
python scripts/dataset/export_rtlcoder_teacher_answer_batches.py \
  --input data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --output-dir data/review/rtlcoder_teacher_answer_batches \
  --force \
  --json
```

Equivalent generic export:

```bash
python scripts/dataset/export_rtl_answer_teacher_batches.py \
  --input data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --output-dir data/review/rtlcoder_teacher_answer_batches \
  --batch-size 10 \
  --force \
  --json
```

Expected batch outputs:

```text
data/review/rtlcoder_teacher_answer_batches/batch_001.json
data/review/rtlcoder_teacher_answer_batches/batch_002.json
...
```

Each batch includes:

- batch metadata
- `prompt_template`
- `expected_source_ids`
- exact `rtl_task_v0.1` rows with no mutation

## Step 2: Send each batch to the teacher model

For each exported batch:

1. Open [llm_rtl_answer_generation_prompt.md](llm_rtl_answer_generation_prompt.md).
2. Paste the prompt template into the teacher chat.
3. Paste one batch JSON file.
4. Ask for JSON only, with one `rtl_answer_v0.1` answer per input row in the same order.

The teacher should use only the supplied task artifacts. It must not invent simulation, lint, synthesis, timing, toggle, activity, power, equivalence, or formal results.

## Step 3: Save returned answers locally

Save each returned batch under:

```text
data/review/rtlcoder_teacher_answer_returns/
```

Example:

```text
data/review/rtlcoder_teacher_answer_returns/batch_001_answers.json
```

## Step 4: Validate returned answers

Use the RTLCoder validation wrapper:

```bash
python scripts/dataset/validate_rtlcoder_teacher_answers.py \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --answers data/review/rtlcoder_teacher_answer_returns/batch_001_answers.json \
  --output-md data/review/rtlcoder_teacher_answer_returns/batch_001_validation.md \
  --output-json data/review/rtlcoder_teacher_answer_returns/batch_001_validation.json \
  --strict \
  --json
```

This validates structure, source IDs, required fields, conservative claim levels, and unsupported tool-result claims.

## Step 5: Merge task and answer rows

After validation passes, merge tasks and answers into teacher-distill-style draft rows:

```bash
python scripts/dataset/merge_rtlcoder_teacher_distill_rows.py \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --answers data/review/rtlcoder_teacher_answer_returns/all_answers.jsonl \
  --output data/review/rtlcoder_teacher_answer_draft_rows.jsonl \
  --strict \
  --json
```

The merged rows remain:

- `review_status: teacher_distilled_unreviewed`
- `approval_status: not_approved`
- `promotion_allowed: false`

They preserve `system`, `user`, and `assistant` role order, where the user message contains the exact `rtl_task_v0.1` object and the assistant message contains the exact `rtl_answer_v0.1` object.

## Step 6: Prepare a teacher-distill dataset extension later

The merged draft rows are still local draft data. They can later feed a teacher-distill dataset extension workflow, but they are still unreviewed and not approved.

Keep everything under `data/review/` until a later human review and provenance check is complete.

## Step 7: Keep everything unreviewed and not approved

Throughout this flow:

- keep `review_status` unreviewed or teacher-distilled-unreviewed
- keep `approval_status: not_approved`
- keep `promotion_allowed: false`
- do not treat teacher answers as human truth
- do not promote anything to golden

## Scaled RTLCoder synthetic bug run

The 500-row pilot remains preserved and should not be overwritten:

- `data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl`
- `data/review/rtlcoder_teacher_answer_batches/`
- `data/review/rtlcoder_teacher_answer_draft_rows.jsonl`

For the scaled draft run, use the larger synthetic bug input and export size 20 teacher batches:

```bash
python scripts/dataset/export_rtlcoder_teacher_answer_batches.py \
  --input data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl \
  --output-dir data/review/rtlcoder_teacher_answer_batches_1000 \
  --batch-size 20 \
  --force \
  --json
```

This scaled flow assumes:

- the raw import was expanded from 500 pilot rows to 3000 rows,
- the normalized reference task set was regenerated from the 3000-row raw index,
- the synthetic bug generator targeted 1000 rows with `--target-bug-rows 1000`,
- all generated tasks, answers, validations, and merged rows remain unapproved draft data under `data/review/`.

Only combine RTLCoder-derived rows with VerilogEval after the teacher answers have been generated, validated, and merged into draft teacher-distill rows.

## Warnings

- Synthetic candidate bugs are regex-generated text mutations, not proven functional bugs.
- Teacher answers are still distillation, not human truth.
- No generated row should be promoted to golden without human review and license/provenance confirmation.
- RTLCoder provenance remains externally sourced and unverified unless a maintainer confirms it separately.
