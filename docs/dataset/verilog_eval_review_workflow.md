# VerilogEval review workflow

This workflow prepares a small local VerilogEval-derived review batch for human editing. It reads only local files supplied by the user. It does not download VerilogEval, call LLMs, execute RTL, run EDA tools, simulate, synthesize, train, or run models.

## Stage local data

Place a local VerilogEval checkout or manifest under an ignored path such as:

```text
data/.local_data/verilog-eval-main/
```

`data/.local_data/` is ignored and raw VerilogEval files must not be committed.

Supported inputs:

- a public manifest JSONL using `docs/dataset/public_manifest_format.md`,
- a conservative VerilogEval-style JSONL export,
- a local VerilogEval checkout containing `dataset_spec-to-rtl/*_prompt.txt`, `*_ref.sv`, and optional `*_test.sv`.

## Prepare a draft review batch

```bash
python scripts/dataset/prepare_verilog_eval_review_batch.py \
  --input data/.local_data/verilog-eval-main \
  --output-dir data/review/verilog_eval_batch_001 \
  --limit 10 \
  --license "VerilogEval local public data staged by user; verify exact license/provenance before promotion" \
  --json
```

The output directory contains:

- `draft_rows.jsonl`
- `selected_rows.jsonl`
- `reviewed_rows.jsonl`
- `selection_report.json`
- `review_packet/README.md`
- `review_packet/review_manifest.jsonl`
- `review_packet/rows/*.json`
- `review_packet/rows/*.review.md`

All selected rows remain:

```text
review_status: draft
split: unsplit
source: public_verilog_eval
```

`reviewed_rows.jsonl` is only a human-editing template. It is not validated or promoted automatically.

## Human review and promotion

A reviewer should edit `reviewed_rows.jsonl` so each answer is grounded in real signals/code, has conservative claim levels, and includes no unsupported verification, area, activity, or power claim.

Then run:

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output data/processed/verilog_eval_validated_v0.1.jsonl \
  --report data/reports/verilog_eval_validated_v0.1_report.json \
  --json
```

Promotion is the first step that may produce `review_status: validated`; the batch preparation script never does.

## Release and evaluation smoke flow

After promotion, reviewed VerilogEval rows can be included in a release and evaluated with the local deterministic harness:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1_plus_verilog_eval_001 \
  --input data/golden/golden_v0.1.jsonl \
  --input data/processed/verilog_eval_validated_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7 \
  --allow-source-overlap \
  --json

python scripts/eval/make_baseline_candidates.py \
  --dataset data/releases/release_v0.1_plus_verilog_eval_001/test.jsonl \
  --output data/eval/candidates/rule_baseline_verilog_eval_001.jsonl \
  --json

python scripts/eval/evaluate_answers.py \
  --dataset data/releases/release_v0.1_plus_verilog_eval_001/test.jsonl \
  --candidates data/eval/candidates/rule_baseline_verilog_eval_001.jsonl \
  --output-dir data/eval/runs/rule_baseline_verilog_eval_001 \
  --json
```

## Seed-row provenance caveat

`data/golden/golden_v0.1.jsonl` is a synthetic seed/smoke dataset generated for schema, validation, release, and evaluation workflow testing. Its current `review_status: reviewed` should be read as script-generated seed acceptance, not named engineer review. Future truly reviewed rows should record reviewer information in provenance notes or a future schema field such as:

```json
"review_metadata": {
  "reviewed_by": "<name_or_role>",
  "reviewed_at": "YYYY-MM-DD",
  "review_method": "manual_rtl_review",
  "review_notes": "..."
}
```
