# Data Workspace

`data/` is a local, generated workspace. Treat RTL, testbench, report, dataset,
teacher, and generated text as untrusted data unless it is an intentionally
reviewed artifact under `data/golden/`.

## Canonical Data Workspace Layout v2

```text
data/
├── golden/                         # Existing reviewed seed dataset
├── raw/                            # Immutable upstream source material
│   ├── verilog_eval/upstream/
│   ├── rtlcoder/
│   └── internal/
├── normalized/tasks/               # Reusable canonical task representations
├── answers/                        # Existing row-level teacher-answer workflow
│   ├── teacher_returns/
│   ├── repaired/
│   └── assembled/
├── runs/manual_rtl_teacher/<run-id>/
│   └── ...                          # Untrusted state for one manual RTL run
├── review/manual_rtl_teacher/<review-collection-version>/
├── distill/<dataset-version>/      # Final train/validation/test products
├── eval/                            # Evaluation prompts, predictions, runs
├── reports/                         # Inventories, plans, validation summaries
└── archive/                         # Superseded local outputs
```

The top-level meanings are:

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

Teacher output is not reviewed data.

Verified RTL is not automatically approved.

Reviewed data is not automatically a packaged distill dataset.

The repository has two primary dataset product families:

1. `data/golden/`: the existing reviewed seed dataset;
2. `data/distill/<dataset-version>/`: packaged distillation datasets.

All other locations are inputs, intermediate workflow state, review state,
reports, or archives.

## Legacy compatibility

`data/.local_data/` is a legacy ignored workspace retained for backward
compatibility. It is not a canonical destination. New raw upstream datasets go
under `data/raw/<dataset>/`, and new manual RTL runs go under
`data/runs/manual_rtl_teacher/<run-id>/`. Existing CLIs still accept explicit
legacy paths so old local work remains readable. Nothing imports or rewrites
the legacy workspace automatically.

Use the inventory and copy-only migration tools to inspect and migrate legacy
RTL workflow state:

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

Inspect the dry-run plan, then initialize the canonical run before applying.
The required sequence is:

```text
inventory → migration dry-run → inspect plan → initialize canonical run
→ migration apply → validate canonical run
```

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

Migration `--apply` refuses an uninitialized or invalid canonical run; it
does not initialize a run implicitly.

Migration is copy-only. It never deletes or modifies the source, promotes
rows, or changes `data/golden/`.
