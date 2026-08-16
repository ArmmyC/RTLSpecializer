# RTL Generation Bounded Batch v0.2

## Purpose

Define the next train-only expansion after the verified five-row smoke and
qualified-16 milestones. This specification keeps selection, correction,
qualification, normalization, teacher generation, verification, repair, and
packaging as separate bounded gates.

The repository controls are metadata-only. They do not call a model, execute
RTLBench, run HDL, or invoke a compiler, simulator, Docker, or other EDA tool.
Those operations remain explicit operator-boundary tasks.

## Batch boundary

Each expansion selects between 20 and 40 new source IDs from the frozen train
pool. Validation and test IDs are rejected. A new selection must not reuse the
smoke or an earlier expansion selection unless an explicit lineage policy says
otherwise. The selection records:

- source commit, source-tree hash, inventory hash, and frozen split hash;
- ordered source IDs and task IDs;
- current readiness and blocking reason;
- public-spec selection role, design family, and diversity tags;
- requested and achieved diversity counts;
- unmet advisory targets, including an explicitly recorded
  `counter_or_timer` shortfall when the eligible pool cannot satisfy it.

The selection is immutable after export. The frozen split is never rewritten.

## Correction and qualification

Corrected assets live in a new versioned overlay. Each selected task requires a
standalone `TopModule` testbench authored from public specification and
interface data only. Reference RTL, original reference-only testbench logic,
private paths, and undeclared support files are forbidden.

Static checks must pass before an isolated qualification authorization is
created. Qualification uses one independently authored positive candidate and
at least two public-spec-derived negative mutations per task. A task is
qualified only when the positive candidate passes with zero mismatches and all
negative mutations are detected and rejected with valid runner provenance.

Qualification failures stay in a correction track. They never produce teacher
RTL repair prompts and are not silently included in a later qualified subset.

## Generation gates

Only qualified task IDs enter the public normalization packet set. The
normalization response is an atomic JSON-only exchange preserving IDs, order,
specification, interface, and public uncertainty. After strict validation,
qualified private assets are joined by exact identity and a teacher packet set
is exported without testbench, mutation, reference, runner, or private-path
content.

Teacher responses are validated atomically before handoff preparation. Each
candidate is executed at most once per attempt through the approved isolated
workflow. Only compile failures, functional mismatches, and timeouts may
produce bounded candidate repair packets. Runner, asset, parser, provenance,
privacy, and infrastructure failures remain investigation tracks.

## Packaging and metrics

The package is a new immutable version. A row enters the clean generation set
only when it belongs to the frozen train split, its corrected asset qualified,
its candidate was accepted, compile and simulation passed, the maximum
mismatch count is zero, candidate/evidence hashes match, and runner
provenance is valid. `promotion_allowed` remains `false` and the default
review status is `automated_verified_unreviewed`.

Every batch records the metrics schema
`rtl_generation_batch_metrics_v0.1`, including selected tasks, qualified
assets, first-attempt passes, repair successes, final accepted rows, compile
failures, functional mismatches, timeouts, privacy/provenance failures,
runner failures, and asset failures. The record is train-only and cannot be
written over.

## Current planned lineage

The first v0.2 implementation uses:

```text
selection:          verilog_eval_v003_asset_correction
correction overlay: verilog_eval_assetfix_v004
run namespace:      pilot_006_assetfix_v004_batch20
package namespace:   rtl_generation_batch20_v001
```

The initial selection contains twenty new train IDs and is deliberately more
diverse than the smoke set: combinational/vector logic, sequential edge and
reset behavior, priority/case logic, and small FSMs. Arithmetic/counter
coverage is advisory; the selector must report a shortfall rather than weaken
train-only or bounded-complexity rules.

## Required execution order

```text
select and freeze IDs
→ author versioned reference-free assets
→ static dependency/privacy validation
→ prepare public positive and negative mutation contracts
→ authorize and run one isolated qualification batch
→ freeze qualified and failed lists
→ normalize qualified public tasks
→ assemble qualified assets
→ export teacher packets
→ generate and validate teacher candidates
→ prepare and execute isolated candidate verification
→ repair candidate failures only
→ run nonpublication package preflight
→ authorize one publication
→ validate, hash, freeze, and review the package
```
