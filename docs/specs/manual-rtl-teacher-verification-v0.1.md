# Manual RTL Teacher Generation and Verification Workflow v0.1

## Goal

This feature prepares public manual teacher packets, validates manually saved
candidate JSON, creates a deterministic RTLBench candidate handoff, ingests
strict RTLBench evidence, and exports bounded repair packets. It never calls a
model or API, executes RTL, invokes RTLBench or an EDA tool, runs arbitrary
shell commands, or automatically repairs, reviews, promotes, or marks data
training-ready.

The operator workflow is:

```text
validated rtl_generation_task_v0.1
  -> public initial packet
  -> human LLM interaction
  -> strict candidate validation
  -> private deterministic RTLBench handoff
  -> human isolated RTLBench verification
  -> strict evidence ingestion
  -> bounded repair packet on failure
```

There are at most four total attempts per task. Reference RTL, testbenches,
support files, private hashes, workspaces, raw logs, and evidence never enter
teacher-visible packets.

## Contracts

The packet schemas are `rtl_teacher_generation_packet_v0.1` and
`rtl_teacher_repair_packet_v0.1`. The manual response is an object containing
exactly `rows`; each row is exactly `rtl_teacher_candidate_v0.1`. Local
validation derives `candidate_id` as:

```python
f"{task_id}_attempt_{attempt:02d}"
```

and computes the SHA-256 of the exact UTF-8 bytes of `candidate.rtl`.

The validated local record is `rtl_teacher_candidate_record_v0.1`. Handoff
plans are `rtl_candidate_verification_plan_v0.1` and emit the current
RTLBench `rtl_candidate_manifest_v0.1` field set for the supported
`verilog_eval_mismatch_v1` profile. Evidence must be exact
`rtl_candidate_evidence_v0.1` and becomes an evidence-aware, non-training-ready
`rtl_generation_attempt_v0.1` row.

## Safety and determinism

All input rows are validated before publication. JSON and JSONL outputs use
stable sorted-key serialization, atomic writes, deterministic packet IDs, and
input order. Inputs, private roots, output ancestry, symlinks, path traversal,
hard-link aliases, stale hashes, output aliases, and unsafe overwrites are
rejected. Candidate RTL receives conservative structural checks only; it is
never parsed, compiled, or simulated by RTLSpecializer.

The private asset root is explicit. Paths in `verification_assets.jsonl` are
resolved relative to that root, not relative to a copied manifest.
