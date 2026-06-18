# Public manifest format

`public-dataset-draft-ingestion-v0.1` imports local public artifacts through a JSONL manifest. Each line is one example. The importer never downloads data, never executes RTL, and never runs EDA tools.

Example:

```json
{"id":"counter_001","source":"public_verilog_eval","license":"see_upstream","design_family":"counter","task_type":"rtl_bug_review","user_goal":"find_correctness_bug","artifacts":{"rtl_code_path":"counter_candidate.v","before_rtl_code_path":null,"after_rtl_code_path":null,"testbench_path":"counter_tb.v","lint_log_path":null,"synthesis_report_path":null,"toggle_report_path":null},"provenance":{"public_dataset_name":"VerilogEval","public_dataset_url":"https://example.invalid/upstream","source_commit":null,"notes":"Local copy supplied manually."}}
```

Required top-level fields:

- `id`: stable source example ID.
- `source`: allowed dataset source such as `public_verilog_eval`, `public_rtllm`, `public_rtllm_2`, or `public_rtlfixer`.
- `license`: non-empty upstream license or review label.
- `design_family`: grouping name used for split isolation later.
- `task_type`: existing `dataset_v0.1` task type.
- `user_goal`: existing `dataset_v0.1` user goal.
- `artifacts`: local artifact paths.
- `provenance`: public source metadata.

Supported artifact path fields:

- `rtl_code_path`
- `before_rtl_code_path`
- `after_rtl_code_path`
- `testbench_path`
- `lint_log_path`
- `synthesis_report_path`
- `toggle_report_path`

At least one artifact path must be non-null and readable as UTF-8 text. Paths are resolved relative to the manifest file directory.

Path safety defaults:

- Absolute artifact paths are rejected unless `--allow-absolute-paths` is set.
- Paths that escape the manifest directory are rejected unless `--allow-outside-root` is set.
- Symlinks resolving outside the manifest directory are rejected by the same outside-root rule.
- Artifact files must be regular files.
- Individual artifact files are limited by `--max-artifact-bytes`, default `1048576`.

Run the importer:

```bash
python scripts/dataset/import_public_dataset.py \
  --adapter manifest \
  --input data/raw_public/example_manifest.jsonl \
  --output data/drafts/public_manifest_draft_v0.1.jsonl \
  --json
```

Generated rows are always:

```text
review_status: draft
split: unsplit
created_by: script
```

They are structurally valid draft rows, not training-ready rows. Review or conversion must happen later before promotion to `validated` or `reviewed`.
