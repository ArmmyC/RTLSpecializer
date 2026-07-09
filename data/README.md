# Data Workspace

Most of `data/` is a local/generated workspace. Treat every file here as untrusted data unless it is an intentionally reviewed artifact under `data/golden/`.

## Main folders

- `golden/`: reviewed seed rows only. Do not drop draft/generated data here.
- `raw/`: original imports and raw source material. Keep these files unedited.
- `normalized/tasks/`: canonical `rtl_task.v0.1` or `rtl_task_v0.1` JSONL files.
- `answers/teacher_returns/`: original teacher-answer batch returns.
- `answers/repaired/`: repaired answer copies created by the repair workflow.
- `answers/assembled/`: one de-duplicated answer JSONL per dataset after repair/assembly.
- `distill/`: generated teacher-distill train/validation/test packaging outputs.
- `eval/`: prompt exports, model predictions, run outputs, and comparisons.
- `reports/`: validation, repair, assembly, inventory, and cleanup-plan reports.
- `archive/`: older generated outputs kept for traceability, not deletion.
- `.local_data/`: legacy local-only raw workspace. Keep its contents uncommitted.

## Placement guide

- Place raw imports from VerilogEval, RTLCoder, or internal sources under `data/raw/<dataset>/`.
- Place normalized task JSONL files under `data/normalized/tasks/`.
- Place teacher-answer returns under `data/answers/teacher_returns/<dataset>/`.
- Place repaired answer copies under `data/answers/repaired/<dataset>/`.
- Place assembled answer JSONL files under `data/answers/assembled/`.
- Place teacher-distill packaging outputs under `data/distill/<dataset_name>/`.
- Place evaluation prompt/output files under `data/eval/`.
- Place generated reports under `data/reports/`.

## Safety rules

- Never commit proprietary RTL, private tool logs, credentials, PDK/fab data, or license-unclear source material.
- Never promote draft/synthetic/generated data to `data/golden/` without human review.
- Never mark rows approved or human-reviewed just because they were normalized, repaired, assembled, or packaged.
- Generated dataset JSONL, reports, and eval outputs should remain ignored unless a maintainer explicitly intends to publish a reviewed artifact.

See [data_workspace_layout.md](../docs/dataset/data_workspace_layout.md) and [dataset_guidelines.md](../docs/dataset/dataset_guidelines.md) for the recommended layout and workflow rules.
