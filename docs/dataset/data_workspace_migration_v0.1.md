# Data Workspace Migration v0.1

The migration command inventories known legacy RTL workflow paths and copies
them into one `data/runs/manual_rtl_teacher/<run-id>/` layout. It is opt-in and
copy-only. Sources remain untouched; no row is reviewed, approved, promoted, or
made training-ready by this tool.

## Required sequence

Use this sequence for every migration:

```text
inventory
→ migration dry-run
→ inspect plan
→ initialize canonical run
→ migration apply
→ validate canonical run
```

## Inspect first

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

The workflow document retains its historical v0.1 filename, but current
migration reports use the `data_workspace_migration_plan_v0.2` contract. The
shipped v0.1 schema remains unchanged for historical reports.

Inspect the dry-run plan before creating the destination run. It lists known
mappings, missing optional sources, hashes, collisions, and a classification of
legacy paths. `unknown_paths` contains only genuinely unclassified paths;
`unmapped_legacy_paths` contains understood legacy paths without a mapping;
`out_of_scope_paths` contains explicitly retained historical, review, smoke,
or processed workspaces. `legacy_subtrees` groups those classifications by
legacy source subtree.

Dry-run is the default. Reports contain only repository-relative paths,
categories, counts, sizes, hashes, collisions, and warnings. They do not
contain RTL, testbench text, teacher responses, candidate code, logs, or
credentials.

## Initialize and apply after review

Initialize the exact canonical run after the dry-run plan has been inspected:

```bash
python scripts/dataset/init_manual_rtl_run.py \
  --run-id pilot_001 \
  --source-dataset VerilogEval \
  --runs-root data/runs/manual_rtl_teacher \
  --json
```

`--apply` refuses an uninitialized run, a malformed manifest, an invalid
canonical directory structure, a run whose manifest identity does not match
the requested run ID, any collision, or any nonzero
`summary.blocking_unresolved_count`. Migration never initializes a run implicitly
and does not require unrelated out-of-scope paths to belong to this pilot.

```bash
python scripts/dataset/migrate_legacy_rtl_data_workspace.py \
  --data-root data \
  --run-id pilot_001 \
  --apply \
  --output data/reports/migration/pilot_001_applied.json \
  --json
```

Validate the canonical run only after apply succeeds:

```bash
python scripts/dataset/validate_manual_rtl_run.py \
  --run-root data/runs/manual_rtl_teacher/pilot_001 \
  --json
```

Apply performs complete preflight, rejects source/destination symlinks and
collisions, hashes sources before and after bounded streaming copies, stages
new destination roots, verifies destination hashes, and atomically publishes
the staged roots. A failed copy does not leave a partial destination tree.
Existing identical files are `already_present`; different bytes, file/directory
mismatches, unknown destination entries, and symlinks are hard failures. The
destination run is validated before copying and again after publication; a
failed post-copy validation rolls back the publication and does not write the
applied report.

Known source mappings include the useful full VerilogEval checkout under
`data/.local_data/verilog-eval-main/` (excluding `.git`, caches, credentials,
and temporary files), plus the legacy normalization, private asset, teacher
response, candidate verification, generation-packet, and repair-packet paths.
Missing known sources are reported as optional. Unknown paths are retained in
the plan and are never guessed or copied. Existing older review datasets,
reports, smoke outputs, and processed/heldout workspaces are classified as
out of scope for `pilot_001`; they are neither copied into the run nor used to
block apply.

No unresolved legacy path may be silently ignored. No explicitly out-of-scope
historical path is pulled into `pilot_001`.

The tool never deletes legacy sources, changes `data/golden/`, follows symlinks,
copies `.git/` or caches, calls external processes, or writes a migration
report before successful apply publication.
