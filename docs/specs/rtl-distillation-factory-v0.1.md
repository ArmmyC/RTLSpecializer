# Verified RTL Distillation Factory v0.1

## Goal

Provide local, deterministic control-plane tooling for the manual-LLM RTL
distillation workflow:

```text
staged public source
  -> acquisition manifest and complete inventory
  -> frozen source split
  -> manual LLM task normalization
  -> strict normalized-task validation
  -> manual LLM RTL generation
  -> bounded candidate verification
  -> verified generation SFT rows
```

The repository never calls an LLM or external API, downloads source data,
executes RTL, or runs an EDA tool. Manual packet exchange and the existing
RTLBench verification workflow remain operator-controlled steps.

## Fixed v0.1 policy

- The first source is a locally staged 156-row VerilogEval checkout.
- The upstream commit, license files, clean-checkout state, and canonical
  source-tree hash are required acquisition metadata.
- Every discovered row receives one metadata-only inventory record. Inventory
  records contain hashes and readiness metadata, never source or testbench
  contents or absolute paths.
- Splits are frozen before teacher generation with ratios `0.70/0.15/0.15`,
  seed `7`, and design-family isolation.
- Normalization is performed by manual LLM packet exchange. Local validation
  checks exact preservation and safety; it does not replace semantic
  normalization with a deterministic fallback.
- Teacher generation is initially limited to an explicit five-row,
  executable-ready training allowlist. Candidate retries are bounded to four
  attempts and stop after the first accepted candidate.
- The primary generation dataset contains only accepted training candidates.
  Additional accepted retry variants, when they already exist, may be emitted
  separately after exact and normalized-hash deduplication. No extra candidate
  is generated solely to populate that artifact.
- Rows are marked `automated_verified_unreviewed`, `not_approved`, and
  `promotion_allowed: false`.

## Control artifacts

The control plane produces:

```text
data/reports/inventory/<name>_acquisition.json
data/reports/inventory/<name>_rows.jsonl
data/reports/inventory/<name>_audit.json
data/reports/inventory/<name>_missing.json
data/reports/inventory/<name>_duplicates.json
data/reports/inventory/<name>_license.json
data/reports/validation/<name>_split.json
data/reports/validation/<name>_smoke_ids.txt
data/reports/validation/<name>_smoke_selection.json
data/reports/validation/<name>_asset_correction_selection.json
data/reports/validation/<name>_assetfix_v002_manifest.json
```

The acquisition manifest records the source repository identity, pinned
commit, license-file hashes, source-tree hash, dirty/symlink state, and row
count. The inventory is one JSONL row per source ID and includes deterministic
task identity, content hashes, interface/top-module hints, readiness, and
dependency summary. The split manifest records the inventory hash, algorithm,
seed, ratios, all source IDs, and the exact split assignment.

## CLI contracts

```text
audit_rtl_generation_sources.py
  --input <dataset directory>
  --source-root <checkout root>
  --source-commit <40-hex commit>
  --inventory-output <rows.jsonl>
  --acquisition-output <acquisition.json>
  --missing-output <missing.json>
  --duplicates-output <duplicates.json>
  --license-output <license.json>
  --correction-manifest <optional overlay manifest.jsonl>
  --correction-root <optional overlay root>

freeze_rtl_generation_split.py
  --inventory <rows.jsonl>
  --output <split.json>
  --seed 7
  --train-ratio .70 --validation-ratio .15 --test-ratio .15

export_rtl_generation_normalization_batches.py
  --input <dataset directory>
  --source-root <checkout root>
  --source-commit <commit>
  --inventory <rows.jsonl>
  --split-manifest <split.json>
  --split train|validation|test
  --source-ids-file <optional exact allowlist>
  --correction-manifest <optional private correction manifest>
  --correction-root <optional private correction root>
```

The exporter rejects missing or duplicated allowlist IDs, commit mismatches,
inventory mismatches, and any source ID outside the requested split.

`select_rtl_generation_smoke.py` selects exactly five executable-ready rows
from the training split using a deterministic coverage-first algorithm. It
records selected IDs, coverage, uncovered preference categories, and the
inventory/split hashes.

## Bounded verification-asset corrections

When the upstream testbench depends on a private reference-only module, the
source row remains in the frozen split but is ineligible for generation until
an independently authored correction is validated. The v0.1 correction gate
uses five explicit train IDs, preserves source IDs/task IDs/prompt/reference
hashes, and stores standalone testbenches under an ignored versioned overlay:

```text
data/raw/internal/verilog_eval_assetfix_v002/
  manifest.jsonl
  tasks/<source_id>/testbench.sv
```

Corrected testbenches must instantiate `TopModule` directly, contain no
`RefModule`, reference RTL, includes, package/interface dependencies, support
files, or private paths, and emit exactly one complete
`Mismatches: <non-negative decimal integer>` line for the deterministic
`mismatch_count_v1` contract. The overlay-aware inventory changes only
corrected testbench hashes and readiness; it does not rewrite the upstream
checkout or frozen split.

Correction validation includes metadata-only dependency closure, exact public
interface checks, hash/collision checks, and public-spec-derived negative
mutation contracts. Before teacher packets are released, every recorded
mutation contract must be qualified through isolated RTLBench using an
independently authored public-spec candidate. The passing candidate must
compile and simulate, every mutation must be detected with the expected
mismatch result, and no qualification evidence is ingested as candidate
attempt history. A qualification failure is an asset failure and returns to
the correction workflow. The same teacher is not used to author both a
correction testbench and a candidate RTL response.

The pinned RTLBench mutation verifier may preserve sanitized, deterministic
tool output in a passing row's diagnostics. Qualification validation accepts
only the canonical `Mismatches: <count>` simulation result with its sanitized
`$finish` footer, or a sanitized zero-exit compile warning; unrecognized,
private, or raw diagnostics remain failures.

The bounded v002 normalization control uses the exact five-task allowlist and
preserves its order through public export, manual normalization, assembly,
qualification, and teacher packet export. Strict normalization responses are
JSON objects containing only a `rows` array; rejected responses remain
quarantined and are never edited in place.

## Verified generation package

`package_verified_rtl_generation_dataset.py` joins canonical tasks, candidate
records, evidence, attempt history, and the frozen split. It recomputes all
candidate/evidence hashes and requires accepted evidence, compile pass,
simulation pass, zero mismatches, and valid runner provenance. It never reads
or emits private testbench/reference contents or raw logs.

For the assetfix smoke package, the package command additionally requires the
passed retry report and its immutable qualification binding covering all five
training tasks. The original failed qualification report remains preserved for
forensics and is never an accepted packaging input. Rows without the retry
qualification, corrected-testbench hash binding, accepted compile/simulation
evidence, or zero mismatches are excluded from the clean package.

The output uses a generation-specific SFT row with a standard three-message
envelope. The user content is the public `rtl_generation_task_v0.1` object and
the assistant content is the candidate RTL string. The old review-oriented
`rtl_answer_v0.1` package is not used. The package emits `all.jsonl` for all
accepted training rows, `train.jsonl` for primary rows,
`rejected_rows.jsonl`, `manifest.json`, `statistics.json`, a dataset card,
JSON/Markdown validation reports, and a provenance report. The bounded
assetfix smoke package additionally binds the source acquisition attestation,
base frozen split, readiness overlay, assetfix report, qualified correction
manifest, retry qualification report/binding, candidate evidence, and runner
sidecar. Accepted rows may preserve sanitized zero-exit compile warnings as
metadata; warnings never include raw tool output or private paths and do not
change the acceptance decision.

Packaging recovery is immutable. A failed partial package is recorded outside
its output directory with `consumable: false` and is never overwritten or
renamed. A recovery package must use a new package ID, preserve the failed
parent ID and recovery reason, and carry an authorization artifact permitting
exactly one recovery invocation. Before that invocation, the same canonical
inputs must pass focused regressions, the final repository test suite, and a
true non-publication end-to-end preflight in a directory outside
`data/distill/`. The canonical recovery invocation writes a new package in
`pending_finalization` state with `consumable: false`; it is not consumable
until a complete-run validation, output freeze, and conservative content review
have passed. Finalization validates a staged consumable view and atomically
publishes only the managed metadata changes. The strict validator confirms the
canonical qualification-report hash, qualification binding, pinned five-row
order, split membership, candidate and evidence hash chains, privacy boundary,
and runner provenance. It reports these gates explicitly and reads task
identity from `messages[1]["content"]`, never from the message wrapper.

For later bounded batches, the same packager accepts a
`rtl_generation_qualified_subset_binding_v0.1` alongside the larger
qualification report. Only rows present in that passed subset may be
packaged; failed candidates remain in `rejected_rows.jsonl` with a bounded
repair reason. The base frozen split remains authoritative for train
membership, while the qualified binding proves corrected-asset readiness.

Package privacy scanning is recursive over content values, including nested
lists and metadata values, but does not treat harmless metadata keys or public
RTLBench provenance values as leaked content. Private paths, testbench or
reference text, mutation details, and raw logs remain rejected.

## Tests

Tests use synthetic fixtures only and cover acquisition/inventory hashing,
source-ID completeness, split determinism and isolation, allowlist safety,
normalization packet selection, candidate/evidence joins, verification gates,
deduplication, and private-content exclusion. Run the focused dataset tests,
then `python -m pytest tests/dataset tests/eval`.

The CI workflow collects `tests/dataset tests/eval`, while a repository-root
collection also includes the separate `tests/finetune` suite. The preserved
historical root collection had 540 tests (367 dataset, 145 eval, 28 finetune).
The final source state collects and passes 526 tests for the CI command (381
dataset, 145 eval) and 554 tests for the repository-root command (526 CI-scope
tests plus 28 finetune tests). The earlier 552-versus-524 comparison mixed a
repository-root collection at the prior source revision with execution of the
narrower `tests/dataset tests/eval` scope. The final collection and execution
commands were rerun after the privacy-detector fix; no test directory stopped
being discovered and no regression test was deleted or renamed.
