# RTL Generation Batch-20 Asset Correction v0.1

## Purpose

Define the bounded correction phase after the frozen batch-20 selection. Each
selected row receives a versioned, standalone verification testbench authored
from the public specification and interface only.

This phase does not modify the upstream checkout, its original testbenches, or
reference RTL. It does not call an LLM, generate candidate RTL, or execute
RTLBench, a compiler, simulator, or other EDA tool during static preflight.

## Pinned inputs

The correction overlay consumes:

- `data/reports/inventory/verilog_eval_v001_rows.jsonl`
- `data/reports/validation/verilog_eval_v001_split.json`
- `data/reports/validation/verilog_eval_v002_asset_correction_ids.txt`
- `data/reports/validation/verilog_eval_v002_asset_correction_selection.json`
- the local VerilogEval checkout at the pinned source commit

The selected IDs remain in the frozen order from the selection report. The
overlay uses correction version `assetfix_v003` and source commit
`c498220d0a52248f8e3fdffe279075215bde2da6`.

## Overlay layout

```text
data/raw/internal/verilog_eval_assetfix_v003/
├── manifest.jsonl
└── tasks/
    └── <source_id>/testbench.sv
```

The manifest binds each row to the source ID, task ID, public prompt hash,
original reference and original testbench hashes, corrected testbench hash,
source commit/tree, frozen split hash, correction version, authoring method,
and support-file list. It also records one positive and at least one negative
qualification contract per row.

Before qualification, rows must remain:

```text
dependency_closure = passed
qualification_status = pending_isolated_qualification
verification_readiness = pending_qualification
```

Static dependency closure is not evidence that a testbench is behaviorally
strong.

## Static preflight

Every corrected testbench must be a regular 0600 file with no symlink or hard
link and must:

- declare exactly one `tb` module;
- instantiate `TopModule` exactly once;
- declare no additional module, package, interface, or include dependency;
- contain no private path or reference-module marker;
- avoid nondeterministic system tasks;
- emit the canonical `Mismatches: %0d` result and finish once;
- use no support files unless a future reviewed correction explicitly requires
  one.

The static validator rechecks all hashes, order, train membership, source tree
attestation, and mutation-contract presence. It never executes the testbench.

## Qualification gate

Only after static preflight passes may an operator run one bounded isolated
qualification batch. For each row, an independently authored public-spec
candidate must be accepted and every declared negative contract must be
rejected with valid runner provenance and no unexpected timeout. A mutation
that is accepted returns the row to correction; it does not produce a teacher
RTL repair packet.

No normalization or teacher generation packet may be exported for a row until
its qualification result is recorded as passed.
