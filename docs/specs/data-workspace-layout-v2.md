# Data Workspace Layout v2 and Legacy RTL Workflow Migration v0.1

## Goal

Make the local data lifecycle unambiguous before the first real manual RTL
teacher pilot. `data/golden/` and `data/distill/<dataset-version>/` are the
only dataset product families. Raw source, normalized tasks, teacher output,
verification evidence, review decisions, reports, and archives are not
finished datasets.

## Contracts

The implementation provides strict JSON contracts for:

- `schemas/data_workspace_inventory_v0.1.schema.json`;
- `schemas/data_workspace_migration_plan_v0.1.schema.json`; and
- `schemas/manual_rtl_run_manifest_v0.1.schema.json`.

All paths in reports and manifests are normalized POSIX-relative paths. Reports
contain no timestamps, absolute paths, usernames, hostnames, or source content.
File hashes use streaming SHA-256; directory hashes use sorted relative path,
size, and file-digest tuples.

## Tools

The reusable implementation is `scripts/dataset/data_workspace_layout.py` with
thin CLIs for inventory, run initialization, run validation, and copy-only
legacy migration. Runtime dependencies are Python standard-library only.

Initialization creates the exact empty run layout atomically. `--resume` is
allowed only for a run whose requested run ID, source dataset, workflow,
attempt limit, manifest path map, and required directories are already exact.
Validation rejects symlinks, path escapes, misplaced attempt records, private
artifact leakage into teacher-visible directories, legacy path strings, and
approval claims inferred from verification.

Migration defaults to dry-run. Apply requires an already initialized and valid
canonical run, complete collision/source preflight, bounded streaming copy,
source/destination hash agreement, temporary sibling staging, and atomic
publication. It validates the run again before writing the applied report and
rolls back if that validation fails. It never deletes or modifies source bytes,
overwrites conflicting destinations, follows symlinks, or migrates unknown
paths.

## Compatibility and safety

`data/.local_data/` remains ignored and readable by existing explicit-path
CLIs, but new examples use `data/raw/` and
`data/runs/manual_rtl_teacher/<run-id>/`. Existing dataset schemas and answer
workflows are unchanged. No automatic migration occurs on module import or
normal workflow commands. No model, RTL, RTLBench, EDA, or arbitrary
subprocess is invoked.

Run the focused data-workspace tests before the broader dataset/evaluation
suite, then validate the unchanged golden seed strictly. Do not commit local
raw data, teacher responses, candidates, verification evidence, generated
reports, or migration outputs.
