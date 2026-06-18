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

## Review and promote public drafts

Generate a local review packet:

```bash
python scripts/dataset/prepare_review_packet.py \
  --input data/drafts/public_manifest_draft_v0.1.jsonl \
  --output-dir data/review/public_manifest_batch_001 \
  --json
```

After a human or offline process edits the draft answers into grounded reviewed rows, promote them:

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/public_manifest_batch_001/reviewed_rows.jsonl \
  --output data/processed/public_validated_v0.1.jsonl \
  --report data/reports/public_validated_v0.1_report.json \
  --json
```

Promotion rejects unedited import stubs, uncertain licenses, missing public provenance, private sources, and unsupported claims. It sets accepted rows to `review_status: validated` by default and writes a rejected sidecar plus report JSON. See `docs/dataset/review_promotion_workflow.md`.

## Assemble a release

Build release directories only from validated/reviewed local JSONL inputs:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1 \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7 \
  --json
```

A release writes train/val/test JSONL files, `rejected_rows.jsonl`, `manifest.json` with SHA-256 hashes, `stats.json`, and `dataset_card.md`. Draft/rejected rows, uncertain licenses, duplicate IDs/fingerprints, import stubs, and invalid rows are excluded. Design families are isolated across splits by default to reduce leakage. See `docs/dataset/release_workflow.md`.

## Safety and provenance

Public conversion is offline and review-required. Random public Verilog may be incorrect, malicious, duplicated, unlicensed, or mismatched to its prompt; do not use it without source/license review, schema validation, claim checks, and appropriate engineering checks. Dataset content is untrusted data and is never executed by these tools. Company/private RTL and internal reports must never be committed.

When moving from dataset_v0.1 to dataset_v0.2, create a new migration script instead of editing old rows in place.
