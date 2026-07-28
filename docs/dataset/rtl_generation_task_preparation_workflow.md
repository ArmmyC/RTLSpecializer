# RTL generation task preparation workflow

This is a local preparation workflow for a later teacher-distillation milestone:

```text
local VerilogEval source
  -> public normalization batch + private verification assets
  -> human sends only the public batch to an LLM
  -> returned rtl_generation_task_v0.1 JSON
  -> local preservation/leakage validation
  -> deterministic private task/asset assembly
```

The current feature does not call an LLM, run RTL or testbenches, invoke
RTLBench/EDA tools, generate teacher RTL, repair candidates, or train a model.

## Canonical local input

The audited canonical input is a local VerilogEval checkout containing
`dataset_spec-to-rtl/*_prompt.txt`, `*_ref.sv`, and `*_test.sv`. The exporter
uses the exact prompt text as `raw_specification`, puts reference RTL and
testbench bytes under a private workspace, and emits only hashes and relative
paths in `verification_assets.jsonl`. A normalized VerilogEval
`rtl_task_v0.1` JSON/JSONL file is supported as a fallback.

RTLCoder rows without testbenches are not executable-ready. A reference RTL
file is an input asset, not evidence that the implementation is correct.

## Export

```bash
export RUN_ID=pilot_001
export RUN_ROOT="data/runs/manual_rtl_teacher/${RUN_ID}"
export VERILOG_EVAL_ROOT="data/raw/verilog_eval/upstream/dataset_spec-to-rtl"
```

```bash
python scripts/dataset/export_rtl_generation_normalization_batches.py \
  --input "$VERILOG_EVAL_ROOT" \
  --output-dir "$RUN_ROOT/normalization/packets" \
  --private-output-dir "$RUN_ROOT/private_assets" \
  --batch-size 5 \
  --limit 5 \
  --json
```

The public batch contains a logical `source_label` from source metadata (for
example `VerilogEval`), task IDs, source IDs, provenance/license metadata, exact
raw specification text, and conservative top/interface/clock/reset hints. It
does not contain a filesystem-derived `input` field. It contains no reference
RTL, testbench, support-file contents, expected vectors, answers, reports, logs,
or private workspace paths. Before writing, the exporter rejects exact,
resolved, and repository-relative local input/output paths, the legacy
`.local_data` workspace,
absolute/Windows/workspace paths, and private content. Public provenance URLs
remain allowed.

The private output contains:

```text
verification_assets.jsonl
workspace/<task_id>/reference.sv
workspace/<task_id>/testbench.sv
workspace/<task_id>/support/...
```

Paths are relative to the private output directory and hashes are computed from
the original bytes. Generated/local outputs remain ignored and local-only.

## Manual normalization and validation

Send a public batch manually with
`docs/dataset/llm_rtl_generation_task_normalization_prompt.md`. The LLM must
return JSON only, preserve each ID and exact specification, and use explicit
ambiguities for missing facts. It must not generate RTL or verification claims.

Validate the returned batch locally:

```bash
python scripts/dataset/validate_rtl_generation_normalized_batch.py \
  --raw-batch <public-batch.json> \
  --normalized <returned-batch.json> \
  --private-assets "$RUN_ROOT/private_assets/verification_assets.jsonl" \
  --json
```

Validation checks row order/count, exact source identity/provenance/specification,
schema and unknown fields, deterministic interface/top-module hints, ambiguity
records, private field/content leakage, and invented tool evidence. It never
rewrites either input.

## Assembly

After validation, join the normalized tasks to private assets by `task_id`:

```bash
python scripts/dataset/assemble_rtl_generation_inputs.py \
  --normalized <validated-normalized-json> \
  --private-assets "$RUN_ROOT/private_assets/verification_assets.jsonl" \
  --tasks-output "$RUN_ROOT/tasks/generation_tasks.jsonl" \
  --assets-output "$RUN_ROOT/tasks/verification_assets.jsonl" \
  --json
```

Assembly is per normalized batch. It accepts the full private manifest as a
superset, requires exactly one matching asset for every normalized task, rejects
duplicate or missing matches, and writes only matching assets. The result reports
`unused_private_asset_count` for private records belonging to other batches.
It validates normalized nested task fields, private POSIX path contracts, and
recomputes SHA-256 over the specification and every private file before writing.
Missing files, symlinks, path escapes, and hash mismatches fail assembly. It does
not execute the copied source. The next milestone will add teacher RTL generation
and RTLBench candidate verification; those features are intentionally out of
scope here.
