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

Use [data_workspace_migration_v0.1.md](data_workspace_migration_v0.1.md) for
the safe copy-only migration procedure and
[../../docs/specs/data-workspace-layout-v2.md](../specs/data-workspace-layout-v2.md)
for the implementation contract.
