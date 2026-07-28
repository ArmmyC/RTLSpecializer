# Data Workspace Layout v2

RTLSpecializer uses a dataset-first workspace with two product families:

1. `data/golden/`: the existing intentionally reviewed seed dataset;
2. `data/distill/<dataset-version>/`: packaged train, validation, and test
   distillation datasets.

Everything else is source material, intermediate workflow state, review state,
evaluation state, a report, or an archive. A path is never a dataset product
merely because it contains valid-looking JSONL.

## Canonical structure

```text
data/
├── README.md
├── golden/
├── raw/
│   ├── verilog_eval/upstream/
│   │   ├── LICENSE
│   │   ├── README.md
│   │   └── dataset_spec-to-rtl/
│   ├── rtlcoder/
│   └── internal/
├── normalized/tasks/
├── answers/
│   ├── teacher_returns/
│   ├── repaired/
│   └── assembled/
├── runs/
│   └── manual_rtl_teacher/
│       └── pilot_001/
│           ├── run_manifest.json
│           ├── normalization/{packets,responses}/
│           ├── tasks/{generation_tasks.jsonl,verification_assets.jsonl}
│           ├── private_assets/{verification_assets.jsonl,workspace}/
│           ├── teacher/{packets,responses,candidate_records.jsonl}
│           ├── verification/{attempt_01,attempt_02,attempt_03,attempt_04,generation_attempts.jsonl}
│           ├── repairs/{attempt_02,attempt_03,attempt_04}
│           ├── review/{queue.jsonl,decisions.jsonl,summary.json}
│           └── reports/
├── review/manual_rtl_teacher/<review-collection-version>/
├── distill/<dataset-version>/{train.jsonl,validation.jsonl,test.jsonl,manifest.json}
├── eval/
├── reports/{inventory,migration,validation,repair,assembly}/
└── archive/
```

The meanings of the top-level folders are fixed:

- `golden/`: existing intentionally reviewed seed dataset;
- `raw/`: immutable upstream source material;
- `normalized/`: reusable canonical task representations;
- `answers/`: existing row-level teacher-answer workflow;
- `runs/`: local untrusted state for a specific workflow run;
- `review/`: reviewed candidate collections waiting for explicit promotion or packaging;
- `distill/`: final packaged train/validation/test distillation datasets;
- `eval/`: evaluation prompts, predictions, runs, and comparisons;
- `reports/`: inventories, migration plans, validation results, and summaries;
- `archive/`: superseded local outputs retained for traceability.

Teacher output is not reviewed data. Verified RTL is not automatically
approved. Reviewed data is not automatically a packaged distill dataset.

## Boundaries

`data/.local_data/` is a legacy ignored workspace preserved for backward
compatibility. New commands and examples must not create files there. Place
raw upstream data under `data/raw/<dataset>/` and initialize new manual RTL
runs under `data/runs/manual_rtl_teacher/<run-id>/`. Existing commands remain
backward-compatible when an operator supplies an explicit legacy path.

The layout tools do not download data, call an LLM, execute RTL or testbenches,
invoke RTLBench or EDA tools, inspect file contents as executable material, or
automatically review, approve, promote, or package a candidate.

## Migration sequence

Follow this order:

```text
inventory
→ migration dry-run
→ inspect plan
→ initialize canonical run
→ migration apply
→ validate canonical run
```

```bash
python scripts/dataset/inventory_data_workspace.py \
  --data-root data \
  --output data/reports/inventory/data_workspace_inventory.json \
  --json

python scripts/dataset/migrate_legacy_rtl_data_workspace.py \
  --data-root data \
  --run-id pilot_001 \
  --dry-run \
  --output data/reports/migration/pilot_001_plan.json \
  --json
```

Inspect the plan, then initialize and apply:

```bash
python scripts/dataset/init_manual_rtl_run.py \
  --run-id pilot_001 \
  --source-dataset VerilogEval \
  --runs-root data/runs/manual_rtl_teacher \
  --json

python scripts/dataset/migrate_legacy_rtl_data_workspace.py \
  --data-root data \
  --run-id pilot_001 \
  --apply \
  --output data/reports/migration/pilot_001_applied.json \
  --json

python scripts/dataset/validate_manual_rtl_run.py \
  --run-root data/runs/manual_rtl_teacher/pilot_001 \
  --json
```

Migration `--apply` refuses an uninitialized or invalid canonical run and
never initializes one implicitly. Current migration reports use
`data_workspace_migration_plan_v0.2`; the v0.1 schema remains unchanged for
historical reports. `unknown_paths` plus `unmapped_legacy_paths` are blocking
unresolved paths, while `out_of_scope_paths` are informational.

Migration plans distinguish genuinely unknown paths from recognized legacy
paths that are unmapped or explicitly out of scope. Only
`summary.blocking_unresolved_count` and collisions block apply. The full useful
VerilogEval checkout is the raw-source migration; older review datasets,
reports, smoke outputs, and processed/heldout workspaces remain outside a
manual RTL pilot.

No unresolved legacy path may be silently ignored. No explicitly out-of-scope
historical path is pulled into `pilot_001`.

Use [data_workspace_migration_v0.1.md](data_workspace_migration_v0.1.md) for
the safe copy-only migration procedure and
[../../docs/specs/data-workspace-layout-v2.md](../specs/data-workspace-layout-v2.md)
for the implementation contract.
