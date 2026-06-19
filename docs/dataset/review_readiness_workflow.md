# Review batch readiness workflow

The readiness checker is a read-only gate between manual editing and promotion. It does not review answers, rewrite rows, execute supplied artifacts, promote data, or change review status.

After manually editing `reviewed_rows.jsonl`, run:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --json
```

The checker strictly validates both dataset files, rejects duplicate IDs, and reports missing or extra reviewed rows. For each matching ID it compares the assistant answer with the selected draft, detects import/stub answers, and applies the same public-data quality gates used by promotion. Unchanged imported stubs remain draft material and must not be promoted.

The JSON report is suitable for scripts. The Markdown report summarizes ready rows, rows needing work, common promotion errors, and a suggested next action for each row. Both files stay local when written under the ignored `data/review/` workspace.

Edit the reviewed file and rerun the checker until every intended row is ready. Use `--strict` as an all-ready gate: it exits nonzero for any not-ready, missing, or extra row. Malformed files and duplicate IDs fail in either mode.

Only after all intended rows are ready should a human explicitly run promotion:

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output data/processed/verilog_eval_validated_v0.1.jsonl \
  --report data/reports/verilog_eval_validated_v0.1_report.json \
  --json
```

Readiness is a preflight report, not approval. Human review and license/provenance approval remain required.

For an all-ready batch, the guarded [reviewed batch finalization workflow](finalize_reviewed_batch_workflow.md) can run promotion, release assembly, conservative baseline generation, and deterministic evaluation in one local command. It still does not replace human review or authorize publication.
