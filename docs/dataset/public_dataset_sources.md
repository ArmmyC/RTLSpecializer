# Public dataset sources

v0.1 supports local-only draft ingestion for manually supplied public RTL artifacts. The importer intentionally does not download datasets, clone repositories, execute RTL, run EDA tools, or call external LLMs.

Supported adapters:

- `manifest`: stable JSONL contract documented in `docs/dataset/public_manifest_format.md`.
- `verilog_eval`: delegates to `<input>/manifest.jsonl` when present; otherwise fails with a manifest-format pointer.
- `rtllm`: delegates to `<input>/manifest.jsonl` when present; otherwise fails with a manifest-format pointer.
- `rtlfixer`: delegates to `<input>/manifest.jsonl` when present; otherwise fails with a manifest-format pointer.

For local-only inspection of the external RTLCoder `Resyn27k.json` file without promoting anything, use:

```bash
python scripts/dataset/import_rtlcoder_dataset.py \
  --input D:/ArmmyWorkspace/SiliconCraft/external_datasets/RTL-Coder/dataset/Resyn27k.json \
  --json
```

This importer writes a raw review index plus Markdown/JSON reports under `data/review/`, marks each row as `external_rtlcoder_gpt_generated_unverified`, defaults to the first 500 rows for a pilot pass, and never promotes or assumes correctness.

To continue from the RTLCoder raw index into normalized reference `rtl_task_v0.1` rows and then deterministic synthetic bug candidates, use:

```bash
python scripts/dataset/normalize_rtlcoder_raw_index.py \
  --input data/review/rtlcoder_raw_index_v0_1.jsonl \
  --output data/review/rtlcoder_rtl_task_v0_1_reference_draft.jsonl \
  --report-md data/review/rtlcoder_rtl_task_normalization_report.md \
  --report-json data/review/rtlcoder_rtl_task_normalization_report.json \
  --max-rows 500 \
  --single-module-only \
  --json

python scripts/dataset/synthesize_rtl_bug_variants.py \
  --input data/review/rtlcoder_rtl_task_v0_1_reference_draft.jsonl \
  --output data/review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft.jsonl \
  --report-md data/review/rtlcoder_synthetic_bug_report.md \
  --report-json data/review/rtlcoder_synthetic_bug_report.json \
  --max-source-rows 200 \
  --variants-per-row 1 \
  --seed 42 \
  --json
```

These rows still remain local drafts only. The normalized RTLCoder tasks keep `source_rtl_role: reference_rtl`, the synthetic rows keep the original reference RTL in `artifacts.rtl_code`, and any synthetic candidate RTL is generated only by deterministic text mutation into `artifacts.before_rtl_code`. Neither path proves correctness or licensing.

Place local public artifacts under `data/raw_public/` or another explicit local path, then create a manifest that records dataset name, canonical URL, source commit if known, per-example provenance, and license. Public availability is not equivalent to training permission or correctness: inspect licenses, duplicates, prompts, expected behavior, and artifacts.

Example:

```bash
python scripts/dataset/import_public_dataset.py \
  --adapter manifest \
  --input data/raw_public/example_manifest.jsonl \
  --output data/drafts/public_manifest_draft_v0.1.jsonl \
  --json
```

Imported public rows remain draft-only:

```text
review_status: draft
split: unsplit
```

They validate structurally so later review tools can consume them, but the normal splitter rejects them unless `--allow-unreviewed` is explicitly used. Do not commit large public datasets, private/company RTL, proprietary logs, or uncertain-license material.

To review and promote local public drafts, generate a review packet and then promote an edited JSONL:

```bash
python scripts/dataset/prepare_review_packet.py \
  --input data/drafts/public_manifest_draft_v0.1.jsonl \
  --output-dir data/review/public_manifest_batch_001

python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/public_manifest_batch_001/reviewed_rows.jsonl \
  --output data/processed/public_validated_v0.1.jsonl \
  --report data/reports/public_validated_v0.1_report.json
```

Promotion does not prove correctness. It rejects unedited import stubs and enforces public source, license, provenance, and existing claim-evidence validation gates before writing validated candidate rows. See `docs/dataset/review_promotion_workflow.md`.
