# Bounded RTL generation batch v0.2 operator workflow

This workflow is the operator-facing companion to
`docs/specs/rtl-generation-batch-v0.2.md`. It records the control-plane
commands for the next train-only batch. Commands that exchange with a model or
execute RTLBench are intentionally boundaries for a separately authorized
operator task; they are not run by repository automation.

## Current selection

The first bounded selection is frozen in:

```text
data/reports/validation/verilog_eval_v003_asset_correction_ids.txt
data/reports/validation/verilog_eval_v003_asset_correction_selection.json
```

It is bound to the VerilogEval source commit and frozen split already recorded
by the inventory. The selection contains 20 new train rows and records the
advisory `counter_or_timer` shortfall without weakening the required
diversity, readiness, or split rules.

## Correction boundary

Manually author a new overlay under:

```text
data/raw/internal/verilog_eval_assetfix_v004/
```

Use `tasks/<source_id>/testbench.sv` and an authoring manifest containing one
public-spec positive candidate and at least two public-spec negative mutations
per row. Do not copy or inspect reference RTL while authoring. The existing
manifest creator and static validator are generic when passed the new
selection IDs, `assetfix_v004`, and the new overlay. They must be run before
qualification preparation.

The qualification run should use a new namespace such as:

```text
data/runs/manual_rtl_teacher/pilot_006_assetfix_v004_batch20/
```

The preparation and preflight CLIs create no execution evidence and do not
run HDL. Obtain an explicit isolated-runner authorization only after static
validation succeeds. Execute that qualification batch once, validate the
runner evidence once, and freeze qualified and failed ID lists.

### Qualification-failure retry boundary

When a negative mutation is not detected, diagnose it from the public
specification and sanitized qualification metadata before changing an asset.
An observationally equivalent mutation is replaced in a new, mutation-only
overlay; the original qualification attempt and correction overlay remain
immutable. The retry preparation must contain only the affected tasks, reuse
unchanged positive fixtures and testbenches by hash, and create a separate
qualification-only authorization and preflight. No retry preparation may run
RTLBench or create teacher-generation inputs.

## Generation boundary

After a retry passes, freeze its qualified and failed lists and create a
combined qualification binding in the retry run. Restore the original
selection order before exporting the qualified public packet. The reusable
control-plane command is:

```text
python -m scripts.dataset.rtl_generation_qualified20_normalization \
  --selection-ids <frozen-selection-ids> \
  --base-correction-manifest <assetfix-v004-manifest> \
  --base-correction-root <assetfix-v004-root> \
  --prior-run-root <preserved-qualification-run> \
  --retry-run-root <passed-retry-run> \
  --retry-correction-manifest <mutation-retry-manifest> \
  --output-run-root <new-qualified-normalization-run> \
  --source-input <normalized-public-source> \
  --runs-root data/runs/manual_rtl_teacher
```

The exporter can bind a canonical public dataset/license identity to the
deterministic task IDs recorded by the qualification manifest without editing
the source file. It writes one public normalization batch and private,
hash-bound verification assets; it does not call a model. Only the qualified
ID list may be exported. The normalization exchange remains manual and
JSON-only: preserve the raw response and validate the complete set atomically;
never edit an invalid response in place. After assembly, export teacher
packets and stop for the separately authorized teacher exchange.

Candidate verification is also separately authorized. Each candidate attempt
is executed at most once. Repair packets may be emitted only for candidate
compile failures, functional mismatches, or timeouts. Asset, parser, runner,
privacy, provenance, and infrastructure failures stay outside the candidate
repair track.

## Release boundary

Before publishing a new package, run focused regressions, the full suite, and
a nonpublication package preflight over a temporary output. Create an explicit
one-invocation authorization, publish into a new immutable package namespace,
validate it strictly, hash every output, and retain `promotion_allowed:
false`. Do not overwrite smoke, batch16, or prior failed package lineages.

Record `rtl_generation_batch_metrics_v0.1` with the package or batch report.
The required fields include selected tasks, qualified assets, first-attempt
passes, repair successes, final accepted rows, compile failures, functional
mismatches, timeouts, privacy/provenance failures, runner failures, and asset
failures.
