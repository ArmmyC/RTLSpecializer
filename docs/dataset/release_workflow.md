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
  --allow-source-overlap \
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
- do not duplicate row IDs or full-row fingerprints.

Draft and rejected rows are written to `rejected_rows.jsonl` with their reason and original row.

## Split isolation

By default the builder splits by `design_family` so the same family does not appear in multiple splits. This reduces leakage between train, validation, and test sets. Use `--allow-family-overlap` only for an explicit experiment.

The builder also checks source overlap. By default, the same `source` enum may not appear in multiple splits. This is intentionally strict and can fail tiny smoke-test releases where all rows come from `handwritten_golden`; pass `--allow-source-overlap` when that overlap is intentional and documented. Allowed source overlap is recorded in `stats.json` and emitted as a warning.

Duplicate checks have different meanings:

- Duplicate row IDs and duplicate full-row fingerprints are rejected before splitting.
- Duplicate artifact fingerprints are checked after splitting, once train/val/test membership is known.
- Duplicate artifacts inside a single split are reported as warnings because they do not leak between train/val/test.
- Duplicate artifacts crossing splits fail the release unless `--allow-family-overlap` is explicitly passed.

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

`stats.json` records counts by split, source, task type, design family, review status, claim levels, rejection reason, and duplicate/leakage checks. The leakage summary includes `family_overlaps`, `source_overlaps`, and `artifact_fingerprint_overlaps` with row IDs and split names for debugging.

## Limitations

Release assembly is packaging and validation, not proof. It does not establish RTL correctness, timing safety, area improvement, activity improvement, or power behavior. Those claims remain governed by the existing claim-level and evidence-status policies.

## Evaluation

After building a release, use `scripts/eval/make_baseline_candidates.py` and `scripts/eval/evaluate_answers.py` to run deterministic local scoring on a split such as `test.jsonl`. Evaluation is also offline and does not perform model inference or semantic proof. See `docs/eval/evaluation_harness.md`.
