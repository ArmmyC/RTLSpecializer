# RTL generation train coverage workflow

Verified RTL generation packages are immutable batch outputs.  A complete
VerilogEval train release is assembled only from those packages; it is not
created by re-running candidates or by editing an earlier package.

## Coverage gate

The frozen VerilogEval split contains 111 train, 23 validation, and 22 test
sources.  The coverage validator accepts repeated immutable package
directories and checks:

- source, task, and candidate IDs are unique across packages;
- every row is a valid verified-generation row and remains `train`;
- package manifests retain the pinned source commit, source-tree hash, and
  frozen-split hash;
- `promotion_allowed` remains `false`;
- reference-supply and private-content checks remain clear; and
- the covered source IDs are compared with the ordered frozen train list.

Example:

```bash
python scripts/dataset/validate_rtl_generation_train_coverage.py \
  --split data/reports/validation/verilog_eval_v001_split.json \
  --package-dir data/distill/rtl_generation_smoke_v001_retry_02 \
  --package-dir data/distill/rtl_generation_batch16_v001_complete \
  --package-dir data/distill/rtl_generation_batch20_v001_complete \
  --package-dir data/distill/rtl_generation_batch24_v001_attempt04_accepted22 \
  --output data/reports/validation/verilog_eval_generation_train_coverage_v001.json \
  --json
```

## Union release

Run a nonpublication preflight in a temporary directory before the canonical
release path:

```bash
python scripts/dataset/assemble_rtl_generation_train_release.py \
  --split data/reports/validation/verilog_eval_v001_split.json \
  --package-dir data/distill/rtl_generation_smoke_v001_retry_02 \
  --package-dir data/distill/rtl_generation_batch16_v001_complete \
  --package-dir data/distill/rtl_generation_batch20_v001_complete \
  --package-dir data/distill/rtl_generation_batch24_v001_attempt04_accepted22 \
  --preflight-only \
  --preflight-dir /tmp/rtl-generation-train-preflight \
  --coverage-report data/reports/validation/verilog_eval_generation_train_coverage_v001.json \
  --json
```

The canonical union command is run once into a new, absent directory after
the preflight passes.  It writes 111 train rows, zero validation/test rows,
and zero rejected rows.  The union inherits the verification provenance of
its source packages and remains experimental: `training_allowed` may be true,
but `promotion_allowed` stays false until a separate review policy changes it.

This control plane does not call a model, execute RTL, or run an EDA tool.
The next task after selecting the remaining train sources is manual asset
authoring and qualification; normalization and teacher generation remain
separate LLM-boundary operations.
