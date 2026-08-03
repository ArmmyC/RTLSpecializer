# RTL Generation Batch-20 Qualification v0.1

## Purpose

This specification defines the isolated qualification gate for the frozen
`assetfix_v003` batch-20 correction overlay. It is a qualification-only
operation. It does not normalize tasks, call a model, generate teacher RTL,
prepare candidate verification handoffs, repair candidates, or package a
dataset.

## Pinned execution

The task runs in the new namespace:

```text
data/runs/manual_rtl_teacher/pilot_004_assetfix_v003/
```

It binds the existing train-only selection and correction overlay:

```text
selection IDs SHA-256: e1927846320a465a49e039e7a8f3518a12424d14e08972a10a1fd232a1d16bbd
correction manifest SHA-256: 2b7cbe7a82f0b73e9960216254565600a9df46bebd66c71fa45f0f401501b8c1
source commit: c498220d0a52248f8e3fdffe279075215bde2da6
source-tree SHA-256: e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f
frozen split SHA-256: 6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608
```

The runner image is the previously validated local image ID:

```text
sha256:004331efd280c2c94a7a25d920f9e008c0f552237d0d66902806a327033ead9b
```

The image must be used with the existing `pilot-docker` profile and the
pinned RTLBench commit `fcad47eb03e469097432229e1285b9239fd23a00`.

## Candidate-based qualification boundary

The pinned runner's fixed inner command is `rtlbench verify-candidates`, so
qualification cases are represented as candidate rows rather than changing
the runner or image contract. There are exactly 60 rows:

- one independently authored positive public-spec candidate per task;
- the two declared negative mutations per task.

The staged runner input contains only `candidate_manifest.jsonl` and
`workspace/`. It contains no reference RTL, original testbench, support file,
instruction file, verification plan, or private path. Each row uses
`mismatch_count_v1`, compile and simulation checks, and `tb` as the testbench
top.

Positive candidates and negative mutations are authored outside the runner
input from public task/interface information only. The preparation tool binds
their hashes and rejects private markers, helper modules, includes, packages,
interfaces, and non-regular files without executing them.

## Qualification result

A task is `qualified` only when its positive candidate is accepted with zero
mismatches and every negative mutation compiles, simulates, reports a positive
mismatch count, and is rejected. Valid non-qualified outcomes remain
classified as candidate or asset results; they do not create teacher repair
prompts.

The derived report and qualified-ID list are immutable outputs. The original
correction manifest remains unchanged and remains the source of the testbench
hash binding.

## Authorization and failure policy

An authorization record is created before execution and records the exact
repositories, working-tree attestations, image ID, input hashes, case count,
command, and one-invocation limit. The execution task records handoff
snapshots, managed Docker resources, runner evidence, sidecar identity,
cleanup, and all output hashes.

Any hash mismatch, unexpected reference/support file, partial evidence,
provenance mismatch, input mutation, or resource leak stops the run. A
systematic missing-result pattern is classified as a runner/parser contract
problem before any asset is declared weak.

No normalization or teacher-generation packet may be exported until a task's
qualification report says `qualified`.

## Qualification-only run layout

`pilot_004_assetfix_v003` is intentionally a qualification-only workspace.
Its authoritative checks are the qualification preparation and qualification
evidence validators, together with the isolated-runner provenance and cleanup
reports.  The generic `validate_manual_rtl_run.py` contract is for a standard
manual teacher run and is not applicable to this layout.  Operators must not
add a fabricated `run_manifest.json` or other teacher-run records merely to
make the generic validator pass.

The next generation run is initialized separately with the standard manual
run layout.  Only task IDs whose qualification rows are `qualified` may be
exported into that run; failed or inconclusive rows remain in the preserved
qualification workspace and require a separately authorized correction retry.
