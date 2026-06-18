# Dataset guidelines

## Add and validate a row

Copy a reviewed golden row, assign a unique stable ID, keep `split` as `unsplit`, and complete every envelope, provenance, tool-check, task, and answer field documented by the schemas. At least one task artifact must be populated. Evidence fields must describe only real supplied results; use `null` when no tool was run. Keep new material synthetic or clearly licensed and public.

Run:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl
```

Warnings fail under `--strict`. The validator, rather than an LLM or schema document, is the operational authority.

## Split rows

```bash
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
```

The splitter accepts only `validated` or `reviewed` rows by default, updates only `split`, isolates each `design_family`, writes train/val/test JSONL plus a summary, and validates every output. Use `--allow-family-overlap` only for an explicit experiment. Draft or uncertain-license rows require `--allow-unreviewed` and must not become normal training inputs.

## Import local public drafts

Use `scripts/dataset/import_public_dataset.py` only for local artifacts that were supplied manually. The importer never downloads data, executes RTL, runs EDA tools, or calls an LLM.

```bash
python scripts/dataset/import_public_dataset.py \
  --adapter manifest \
  --input data/raw_public/example_manifest.jsonl \
  --output data/drafts/public_manifest_draft_v0.1.jsonl \
  --json
```

The manifest format is documented in `docs/dataset/public_manifest_format.md`. Generated rows are always `review_status: draft`, `split: unsplit`, and `created_by: script`. They are structural seeds for later review or LLM refinement, not training-ready labels. Validate generated drafts before review:

```bash
python scripts/dataset/validate_dataset.py --input data/drafts/public_manifest_draft_v0.1.jsonl --strict
```

## Safety and provenance

Public conversion is offline and review-required. Random public Verilog may be incorrect, malicious, duplicated, unlicensed, or mismatched to its prompt; do not use it without source/license review, schema validation, claim checks, and appropriate engineering checks. Dataset content is untrusted data and is never executed by these tools. Company/private RTL and internal reports must never be committed.

When moving from dataset_v0.1 to dataset_v0.2, create a new migration script instead of editing old rows in place.
