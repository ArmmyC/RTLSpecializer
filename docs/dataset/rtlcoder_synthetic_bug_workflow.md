# RTLCoder synthetic bug workflow

This workflow converts the external RTLCoder `Resyn27k.json` dataset into local-only draft task rows, then generates deterministic synthetic buggy-candidate rows for later teacher-answer generation.

The repository still does not:

- download anything,
- call external LLMs or APIs,
- execute RTL or testbenches,
- run lint, simulation, synthesis, equivalence, toggle, or power tools,
- fine-tune models inside this workflow,
- promote anything to golden.

## Flow

```text
external RTLCoder Resyn27k.json
  -> raw review index
  -> normalized reference rtl_task_v0.1 draft rows
  -> synthetic bug rtl_task_v0.1 draft rows
  -> teacher-answer batch export
  -> teacher rtl_answer generation
  -> validation
  -> distill dataset merge
```

## Step 1: Build the raw review index

```bash
python scripts/dataset/import_rtlcoder_dataset.py \
  --input D:/ArmmyWorkspace/SiliconCraft/external_datasets/RTL-Coder/dataset/Resyn27k.json \
  --output-index data/review/rtlcoder_raw_index_v0_1.jsonl \
  --output-report-md data/review/rtlcoder_import_report.md \
  --output-report-json data/review/rtlcoder_import_report.json \
  --limit 500 \
  --json
```

This step only inspects schema, prompt/spec text, RTL text, module names, and rough design families. The rows remain marked as:

```text
provenance: external_rtlcoder_gpt_generated_unverified
```

## Step 2: Normalize reference rtl_task rows

```bash
python scripts/dataset/normalize_rtlcoder_raw_index.py \
  --input data/review/rtlcoder_raw_index_v0_1.jsonl \
  --output data/review/rtlcoder_rtl_task_v0_1_reference_draft.jsonl \
  --report-md data/review/rtlcoder_rtl_task_normalization_report.md \
  --report-json data/review/rtlcoder_rtl_task_normalization_report.json \
  --max-rows 500 \
  --single-module-only \
  --json
```

The normalized rows:

- preserve `instruction_text` as `prompt`,
- preserve `rtl_code` as `artifacts.rtl_code`,
- keep `source_rtl_role: reference_rtl`,
- keep all `tool_checks` null,
- keep `review_status: draft`,
- keep `approval_status: not_approved`,
- keep `promotion_allowed: false`,
- keep external unverified provenance,
- do not invent testbenches, tool results, or correctness claims.

## Step 3: Generate deterministic synthetic buggy candidates

```bash
python scripts/dataset/synthesize_rtl_bug_variants.py \
  --input data/review/rtlcoder_rtl_task_v0_1_reference_draft.jsonl \
  --output data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --report-md data/review/rtlcoder_synthetic_bug_report.md \
  --report-json data/review/rtlcoder_synthetic_bug_report.json \
  --max-source-rows 200 \
  --variants-per-row 1 \
  --seed 42 \
  --json
```

For v0.1, only deterministic text-level mutations are used, and only when a conservative regex pattern is found. The current bug types are:

- `wrong_reset_polarity`
- `wrong_mux_select_polarity`
- `incomplete_comb_assignment`
- `off_by_one_counter_limit`
- `shift_direction_flip`
- `blocking_nonblocking_swap_in_clocked_block`
- `width_truncation_output`
- `wrong_fsm_reset_state`

The synthetic rows:

- keep the original reference RTL in `artifacts.rtl_code`,
- store the mutated buggy candidate in `artifacts.before_rtl_code`,
- mark `design_context.prompt_embedded_candidate_rtl: true`,
- keep `source_rtl_role: reference_rtl`,
- mark `synthetic_bug: true`,
- record `bug_type`, `mutation_summary`, `mutated_signal_names`, `mutation_confidence`, `generated_by`, and `seed`,
- keep `review_status: synthetic_draft`,
- keep `approval_status: not_approved`,
- keep `promotion_allowed: false`.

These rows are still draft inputs. A synthetic mutation is not proof that the candidate is meaningful, realistic, lint-clean, or behaviorally distinct.

## Step 4: Export teacher-answer batches

After you have normalized reference rows or synthetic buggy-candidate rows, export them for manual teacher-answer generation:

```bash
python scripts/dataset/export_rtl_answer_teacher_batches.py \
  --input data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --output-dir data/review/rtlcoder_teacher_answer_batches \
  --batch-size 5 \
  --json
```

## Step 5: Validate returned teacher answers

```bash
python scripts/dataset/validate_rtl_answer_teacher_batch.py \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --answers returned_answers.json \
  --output-md data/review/rtlcoder_teacher_answer_validation.md \
  --output-json data/review/rtlcoder_teacher_answer_validation.json \
  --strict \
  --json
```

## Step 6: Merge clean task/answer rows for distill-style packaging

```bash
python scripts/dataset/merge_rtl_task_answer_rows.py \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --answers returned_answers.json \
  --output data/review/rtlcoder_teacher_answer_draft_rows.jsonl \
  --strict \
  --json
```

That merged output can later feed the teacher-distill dataset packaging workflow.

## Warnings

- RTLCoder rows are external and GPT-generated.
- Correctness is not guaranteed.
- License and provenance remain uncertain until manually confirmed.
- Synthetic bugs are generated by text mutation only, not proof.
- Do not promote RTLCoder-derived rows to golden without human review.
