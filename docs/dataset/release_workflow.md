# Dataset release workflow

A dataset release is an auditable train/val/test directory assembled from trusted local `dataset_v0.1` JSONL inputs. Release assembly does not train a model, call an LLM, download data, execute RTL, or run EDA tools.

Release row lifecycle:

- Golden rows are handwritten, reviewed seed examples.
- Draft rows are imported structural seeds and are not training labels.
- Promoted rows are edited public rows that passed promotion gates.
- Release rows are validated/reviewed rows copied into deterministic train/val/test files with only the `split` field changed.

## Build a release

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1 \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7 \
  --json
```

Add more local validated inputs by repeating `--input`.

## Eligibility gates

Rows enter a release only when they:

- use `dataset_v0.1`,
- have `review_status` of `validated` or `reviewed`,
- pass strict validation,
- have a known non-empty license,
- do not contain generic public-import stub answer text,
- do not contain private/proprietary source markers,
- do not duplicate row IDs, full-row fingerprints, or artifact fingerprints.

Draft and rejected rows are written to `rejected_rows.jsonl` with their reason and original row.

## Split isolation

By default the builder splits by `design_family` so the same family does not appear in multiple splits. This reduces leakage between train, validation, and test sets. Use `--allow-family-overlap` only for an explicit experiment.

## Release files

Each release directory contains:

- `train.jsonl`
- `val.jsonl`
- `test.jsonl`
- `rejected_rows.jsonl`
- `manifest.json`
- `stats.json`
- `dataset_card.md`
- `all_accepted.unsplit.jsonl`

`manifest.json` records the release name, schema versions, seed, ratios, input files, row counts, and SHA-256 hashes for generated files. Hashes are computed from file bytes after writing.

`stats.json` records counts by split, source, task type, design family, review status, claim levels, rejection reason, and duplicate/leakage checks.

## Limitations

Release assembly is packaging and validation, not proof. It does not establish RTL correctness, timing safety, area improvement, activity improvement, or power behavior. Those claims remain governed by the existing claim-level and evidence-status policies.
