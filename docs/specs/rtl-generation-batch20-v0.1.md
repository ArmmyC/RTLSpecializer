# RTL Generation Batch-20 Selection and Review Gate v0.1

## Purpose

Extend the verified RTL distillation factory with a new, bounded correction
selection without changing the v0.1 five-row smoke baseline.

This selection phase is metadata-only. It does not author testbenches, call an
LLM, execute RTLBench, run RTL or EDA tools, generate candidates, or package
data.  The follow-on correction phase is specified separately in
`docs/specs/rtl-generation-batch20-correction-v0.1.md`.

## Pinned selection

The selector binds the VerilogEval commit, source-tree hash, base inventory
hash, and frozen split hash. It selects exactly twenty distinct `train` rows
that are currently `needs_testbench` because their original testbench depends
on a reference-only module. Existing smoke IDs are excluded.

The report records the public-spec diversity tags, requested and achieved
coverage, and any advisory category that is unavailable under the bounded
complexity policy. It contains hashes and metadata only; it never emits
reference RTL, testbench text, support content, or private paths.

The correction overlay is versioned independently as `assetfix_v003` and the
future generation lineage is `verilog_eval_generation_v002`.

## Human review gate

The five-row smoke package remains `automated_verified_unreviewed` until an
actual human reviewer supplies a record tied to its exact package tree hash.
The validator checks reviewer identity, timestamp, method, all five row
identities and candidate hashes, per-row content assertions, derived row
decisions, explicitly acknowledged exceptions, and `promotion_allowed:
false`. Automated review assistance is not a human review record and cannot
satisfy this gate. The current record format is
`rtl_generation_human_review_v0.2`.

## Verification

Run the selector regression tests, the full root and CI-scope suites,
`compileall`, and `git diff --check`. The frozen split and all prior smoke and
failed-package outputs must remain byte-for-byte unchanged.
