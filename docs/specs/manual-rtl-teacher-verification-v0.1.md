# Manual RTL Teacher Generation and Verification Workflow v0.1

## Goal

Prepare public manual teacher packets, validate manually saved candidate JSON,
create a deterministic RTLBench handoff, ingest strict RTLBench evidence, and
export bounded repair packets. The implementation is file-based and manual: it
does not call a model or API, expose an endpoint, invoke a subprocess, execute
RTL, invoke RTLBench or an EDA tool, or automatically review, repair, promote,
or mark data training-ready.

## Required workflow

```text
public task
  -> initial packet
  -> manual LLM interaction
  -> private-boundary candidate validation
  -> candidate-record append
  -> attempt-specific RTLBench handoff
  -> manual isolated verification
  -> evidence ingestion
  -> attempt-specific repair packet
  -> repeat through attempt 4 or stop on acceptance
```

The operator must use separate handoff directories such as
`data/.local_data/rtl_candidate_verification/run_001` and `run_002`, and
separate repair directories:

```text
data/review/rtl_teacher_repair_packets/attempt_02
data/review/rtl_teacher_repair_packets/attempt_03
data/review/rtl_teacher_repair_packets/attempt_04
```

## Contracts and invariants

- Initial and repair packet IDs match the exact deterministic format and their
  digest is recomputed from packet kind, ordered task IDs, target attempt, and
  prompt version.
- Candidate validation requires both `--private-assets` and
  `--private-assets-root`. Missing manifests, roots, symlinked/non-directory
  roots, task/private identity mismatches, stale hashes, and private-content
  contamination fail closed.
- Candidate IDs are derived as
  `f"{task_id}_attempt_{attempt:02d}"`. Candidate records are globally ordered
  by `task_id`, `attempt`, `candidate_id` across repeated append operations.
- Handoff filters `--attempt 1..4` and repeatable `--candidate-id` are applied
  after complete input validation. Unknown IDs, duplicate filters, boolean
  attempt values, and empty selections are errors.
- Evidence ingestion defaults to refusing an existing output. `--append`
  validates all existing attempts before combining them; `--overwrite` replaces
  only a valid managed regular output. The two flags are mutually exclusive,
  aliases and symlinks are rejected, and output is atomically written.
- For every task, attempts are exactly contiguous from 1 through the latest
  attempt, never exceed 4, and never occur after acceptance. Candidate IDs and
  `(task_id, attempt)` pairs are unique.
- Repair export binds task, candidate record, and attempt on task/source/top
  identity, deterministic candidate ID, candidate hash, schemas, acceptance,
  failure category, required leaves, mismatch summary, diagnostics, and
  toolchain. It selects the latest valid failed attempt and sets
  `target_attempt = previous_attempt + 1`; it emits nothing after attempt 4 or
  acceptance.

## RTLBench category compatibility

The required failure selector is deliberately identical to the audited RTLBench
implementation:

```python
if accepted:
    return "passed"

for category in (
    "timeout",
    "compile_failure",
    "functional_mismatch",
    "simulation_result_missing",
    "simulation_failure",
    "tool_unavailable",
    "internal_error",
):
    if either required check reason equals category:
        return category

return "partial_failure"
```

In particular, a failed compile leaf does not by itself imply
`compile_failure`; the leaf reason is authoritative. The RTLBench
`internal_error` row shape is valid evidence.

## Fixed v0.1 request profile

The exact requested checks are:

```json
{"compile": true, "simulation": true, "lint": false, "synthesis": false}
```

Lint and synthesis must be exactly unattempted with `reason: "not_requested"`.
Required leaves cannot remain pending. Passing leaves have `reason: null`;
failed leaves have a supported reason; unattempted required leaves use a
supported unavailable reason. Simulation cannot pass unless compile passed.
Accepted mismatch evidence contains at least one report with zero counts and
no timeout. Positive mismatch reports select `functional_mismatch` unless
timeout has priority. A missing simulation result is valid only after an
attempted simulation and a passed compile.

## Validation commands

The focused tests cover packet integrity, private assets, output publication,
candidate selection and ordering, all category fixtures, final leaves,
iterative append/history, repair binding, privacy, and schema structure. Run
the exact commands listed in the repository task instructions before commit.
