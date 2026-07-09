# RTL Answer Dataset Assembly

Use `scripts/dataset/assemble_repaired_rtl_answer_dataset.py` after the answer-file audit/repair workflow to build one clean JSONL answer file from many standalone `rtl_answer.v0.1` files.

This step is needed because broad scans under `data/review/` often see overlapping sources at the same time:

- original batch return files,
- repaired copies under `data/review/repaired_rtl_answer_batches/`,
- previously combined clean files,
- older pilot answer exports.

Those overlaps can create repeated `source_id` values even when the underlying answer rows are still useful draft data. The assembly step selects one deterministic answer row per `source_id`, records what was skipped, validates the selected rows against the matching task rows, and writes a single JSONL file for the next dataset-preparation stage.

## Safety Notes

- Assembled rows are still draft/synthetic teacher-distillation data.
- They are not human-reviewed.
- They are not approved.
- They must not be promoted to `data/golden` by this step.
- The assembler does not run RTL, lint, simulation, synthesis, formal, timing, power, toggle, or other EDA checks.
- The assembler does not repair answer content. Use `scripts/dataset/repair_rtl_answer_files.py` first when cleanup is needed.

## What The Assembler Reads

The assembler reuses the standalone answer-file reader from `scripts/dataset/rtl_answer_file_audit.py` and supports:

- JSON wrapper files with `{ "answers": [...] }`
- JSON arrays
- single-answer JSON objects
- JSONL answer rows

It skips:

- generated chat/train rows with `messages`
- manifests
- validation reports
- repair reports
- dataset cards
- non-answer files

## Priority-Based Selection

When multiple answer rows share the same `source_id`, the assembler keeps exactly one row and never merges fields from different answers.

Default priority:

1. `repaired`
2. `combined`
3. `batch`
4. `other`

Typical interpretation:

- `repaired`: files inside `repaired_rtl_answer_batches`
- `combined`: previously assembled or explicitly clean/merged files
- `batch`: individual teacher-answer batch return files
- `other`: any remaining standalone answer file

Duplicate handling:

- If duplicate rows are JSON-canonical identical, the lower-priority copies are recorded as harmless duplicates.
- If duplicate rows differ, the higher-priority row is still selected deterministically, but the row is flagged with `duplicate_source_id_conflicting_rows` for manual review.

## Strict vs Non-Strict

`--strict` is the review-oriented mode:

- it fails on validation errors,
- it fails on warnings,
- it fails on manual-review flags,
- it fails when tasks are missing answers,
- it fails when duplicate rows conflict.

Without `--strict`, the assembler still writes the output JSONL when it is safe to do so. In non-strict mode, missing answers, extra answers without tasks, and manual-review-only duplicate conflicts are reported but do not block writing the combined draft file.

Hard validation failures still block output writing in any mode. Examples include schema problems, missing required answer fields, missing `source_id`, and conservative-claim validation errors that remain after repair.

## Example Commands

Assemble repaired RTLCoder synthetic-bug answers:

```bash
python scripts/dataset/assemble_repaired_rtl_answer_dataset.py \
  --answers-dir data/review/repaired_rtl_answer_batches \
  --answers-glob "*rtl_answer*v0_1*.json*" \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl \
  --output data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl \
  --report-md data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.md \
  --report-json data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json \
  --strict \
  --json
```

Assemble only the repaired RTLCoder batch-return subtree:

```bash
python scripts/dataset/assemble_repaired_rtl_answer_dataset.py \
  --answers-dir data/review/repaired_rtl_answer_batches/rtlcoder_teacher_answer_returns_1000 \
  --answers-glob "*rtl_answer*v0_1*.json*" \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl \
  --output data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl \
  --report-md data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.md \
  --report-json data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json \
  --json
```

## Reports

The assembler writes both Markdown and JSON reports with:

- files scanned
- answers scanned
- selected answers
- duplicate counts
- missing task answers
- extra answers without tasks
- validation errors
- validation warnings
- manual-review flags
- output path
- output SHA256
- selected source file per `source_id`
- skipped duplicate files per `source_id`

## Feeding Teacher Distill Packaging

The assembled answer JSONL is the safest input for the next packaging step because it removes overlap and locks the answer/task pairing to one row per `source_id`.

Example with ratio split sizes:

```bash
python scripts/dataset/prepare_teacher_distill_dataset.py \
  --tasks data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl \
  --answers data/review/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl \
  --output-dir data/distill/rtlcoder_synthetic_teacher_distill_v0_1 \
  --train-size 0.8 \
  --val-size 0.1 \
  --test-size 0.1 \
  --seed 42 \
  --strict \
  --json
```

That follow-on dataset is still draft teacher-distill data. It remains unreviewed, not approved, and not golden until separate human review and provenance/license confirmation happen.
