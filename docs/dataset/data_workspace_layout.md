# Data Workspace Layout

This repository uses `data/` as a mostly local/generated workspace. The goal is to keep raw imports, normalized tasks, answer-return stages, distill packaging, eval outputs, and reports separated so broad scans do not accidentally mix overlapping stages.

## Target layout

```text
data/
  raw/
    verilog_eval/
    rtlcoder/
    internal/
  normalized/
    tasks/
  answers/
    teacher_returns/
    repaired/
    assembled/
  distill/
    verilog_eval_teacher_distill_v0_1/
    rtlcoder_synthetic_teacher_distill_v0_1/
  eval/
    prompts/
    runs/
    comparisons/
  reports/
    validation/
    repair/
    assembly/
    inventory/
  archive/
    old_review_outputs/
```

## Folder meanings

- `raw/`: original imported data, never edited.
- `normalized/tasks/`: canonical `rtl_task.v0.1` or `rtl_task_v0.1` JSONL files.
- `answers/teacher_returns/`: original teacher answer batch returns.
- `answers/repaired/`: repaired answer copies produced by the repair workflow.
- `answers/assembled/`: one de-duplicated answer JSONL per dataset after assembly.
- `distill/`: generated train/validation/test teacher-distill packaging.
- `eval/`: model prompt exports, model outputs, run artifacts, and comparisons.
- `reports/`: validation, repair, assembly, inventory, and cleanup-plan reports.
- `archive/`: older generated files kept for traceability instead of deletion.

## Practical guidance

- Keep raw source trees under `data/raw/<dataset>/` even if they are large and local-only.
- Keep normalized task JSONL files separate from teacher-answer return files so `source_id` scans stay predictable.
- Keep repaired answer copies separate from assembled answer JSONL files so validation can distinguish overlapping originals from canonical outputs.
- Keep distill packaging outputs under dataset-specific folders so train/validation/test manifests stay together.
- Use `data/archive/old_review_outputs/` for legacy generated files that are still worth preserving but should no longer be scanned as active inputs.

## Safety notes

- `data/golden/` remains the only location for intentionally reviewed seed rows.
- Nothing in this layout implies approval, human review, or promotion.
- RTLCoder rows remain draft/unverified until later human review and provenance/license confirmation.
- Generated JSONL, reports, and eval files should remain gitignored by default.
