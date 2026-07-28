# Data Workspace Migration v0.1

The migration command inventories known legacy RTL workflow paths and copies
them into one `data/runs/manual_rtl_teacher/<run-id>/` layout. It is opt-in and
copy-only. Sources remain untouched; no row is reviewed, approved, promoted, or
made training-ready by this tool.

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

Dry-run is the default. Reports contain only repository-relative paths,
categories, counts, sizes, hashes, collisions, and warnings. They do not
contain RTL, testbench text, teacher responses, candidate code, logs, or
credentials.

## Apply after review

```bash
python scripts/dataset/migrate_legacy_rtl_data_workspace.py \
  --data-root data \
  --run-id pilot_001 \
  --apply \
  --output data/reports/migration/pilot_001_applied.json \
  --json
```

Apply performs complete preflight, rejects source/destination symlinks and
collisions, hashes sources before and after bounded streaming copies, stages
new destination roots, verifies destination hashes, and atomically publishes
the staged roots. A failed copy does not leave a partial destination tree.
Existing identical files are `already_present`; different bytes, file/directory
mismatches, unknown destination entries, and symlinks are hard failures.

Known source mappings include the VerilogEval `dataset_spec-to-rtl` checkout,
its `LICENSE*` and `README*` metadata, and the legacy normalization, private
asset, teacher response, candidate verification, generation-packet, and repair
packet paths. Missing known sources are reported as optional. Unknown paths are
retained in the plan and are never guessed or copied.

The tool never deletes legacy sources, changes `data/golden/`, follows symlinks,
copies `.git/` or caches, calls external processes, or writes a migration
report before successful apply publication.
