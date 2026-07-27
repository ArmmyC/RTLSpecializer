# Manual RTL teacher generation and verification workflow

This is an explicitly manual, local workflow. RTLSpecializer does not call a
model API, expose an endpoint, make automated model calls, invoke a
subprocess, execute RTL or testbenches, invoke RTLBench, or invoke an EDA tool.
The operator performs the LLM interaction and the isolated RTLBench run. A
human reviews evidence before any dataset promotion.

## 1. Export the initial packet

```bash
python scripts/dataset/export_rtl_teacher_generation_packets.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --output-dir data/review/rtl_teacher_generation_packets \
  --batch-size 1 --limit 5 --json
```

Copy `packet_0001.md` into the chosen LLM manually. Save only its returned JSON
as `packet_0001_response.json`; do not save Markdown fences or explanatory
prose. Packet IDs are deterministic over packet kind, ordered task IDs, target
attempt, and prompt version.

## 2. Validate and append candidate records

The production validator always requires both private-boundary options. It
checks private hashes, task/private identity, and reference/testbench/support
contamination before writing a public candidate record.

```bash
python scripts/dataset/validate_rtl_teacher_candidate_batch.py \
  --packet data/review/rtl_teacher_generation_packets/packet_0001.json \
  --response data/.local_data/manual_teacher_responses/packet_0001_response.json \
  --private-assets data/review/rtl_generation_pilot/verification_assets.jsonl \
  --private-assets-root data/.local_data/rtl_generation_verification_assets \
  --output data/review/rtl_generation_pilot/candidate_records.jsonl \
  --json
```

When a repair response is validated, append it to the same candidate-record
file. Existing records are fully validated and the combined file is sorted by
`task_id`, `attempt`, and `candidate_id`.

```bash
python scripts/dataset/validate_rtl_teacher_candidate_batch.py \
  --packet data/review/rtl_teacher_repair_packets/attempt_02/packet_0001.json \
  --response data/.local_data/manual_teacher_responses/repair_02_packet_0001.json \
  --private-assets data/review/rtl_generation_pilot/verification_assets.jsonl \
  --private-assets-root data/.local_data/rtl_generation_verification_assets \
  --output data/review/rtl_generation_pilot/candidate_records.jsonl \
  --append --json
```

## 3. Select one attempt for the RTLBench handoff

Filters are applied only after all input records and private assets have been
validated. The selected candidate-record order is preserved. Use an
attempt-specific directory so a repair round does not silently reverify older
attempts.

Initial verification:

```bash
python scripts/dataset/prepare_rtl_candidate_verification.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --assets data/review/rtl_generation_pilot/verification_assets.jsonl \
  --private-assets-root data/.local_data/rtl_generation_verification_assets \
  --candidates data/review/rtl_generation_pilot/candidate_records.jsonl \
  --attempt 1 \
  --output-dir data/.local_data/rtl_candidate_verification/run_001 \
  --json
```

Attempt 2 verification:

```bash
python scripts/dataset/prepare_rtl_candidate_verification.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --assets data/review/rtl_generation_pilot/verification_assets.jsonl \
  --private-assets-root data/.local_data/rtl_generation_verification_assets \
  --candidates data/review/rtl_generation_pilot/candidate_records.jsonl \
  --attempt 2 \
  --output-dir data/.local_data/rtl_candidate_verification/run_002 \
  --json
```

`--candidate-id` may be repeated for a narrower handoff. Unknown IDs, duplicate
filters, boolean attempt values, and empty selections are rejected. The output
parent may be missing; it is created safely, and staging remains in that same
parent for atomic publication.

## 4. Run the isolated verification manually

Run the exact command in each run directory’s `run_instructions.md` manually
inside a disposable, least-privileged container or VM with no network access
or production secrets:

```bash
rtlbench verify-candidates \
  --manifest <run-dir>/candidate_manifest.jsonl \
  --output <run-dir>/candidate_evidence.jsonl \
  --workspace-root <run-dir>/workspace \
  --work-dir /tmp/rtlbench-candidate-work \
  --force
```

Enforce CPU, memory, process, output, disk, and time limits externally.
RTLSpecializer does not invoke this command.

## 5. Ingest evidence across attempts

The first ingestion rejects an existing output by default:

```bash
python scripts/dataset/ingest_rtl_candidate_evidence.py \
  --plan data/.local_data/rtl_candidate_verification/run_001/verification_plan.jsonl \
  --evidence data/.local_data/rtl_candidate_verification/run_001/candidate_evidence.jsonl \
  --output data/review/rtl_generation_pilot/generation_attempts.jsonl \
  --json
```

Append the next verified attempt without deleting history:

```bash
python scripts/dataset/ingest_rtl_candidate_evidence.py \
  --plan data/.local_data/rtl_candidate_verification/run_002/verification_plan.jsonl \
  --evidence data/.local_data/rtl_candidate_verification/run_002/candidate_evidence.jsonl \
  --output data/review/rtl_generation_pilot/generation_attempts.jsonl \
  --append \
  --json
```

`--append` and `--overwrite` are mutually exclusive. Append loads and fully
validates existing attempts, then atomically writes deterministic order
`task_id`, `attempt`, `candidate_id`. Attempts must be contiguous from 1, no
attempt may exceed 4, and an accepted attempt is terminal. The fixed v0.1
profile requires compile and simulation and leaves lint/synthesis exactly
`not_requested`.

## 6. Export repair packets by round

After a failed attempt, export the latest valid failure to its own round
directory:

```bash
python scripts/dataset/export_rtl_teacher_repair_packets.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --candidates data/review/rtl_generation_pilot/candidate_records.jsonl \
  --attempts data/review/rtl_generation_pilot/generation_attempts.jsonl \
  --output-dir data/review/rtl_teacher_repair_packets/attempt_02 \
  --max-attempts 4 --batch-size 1 --json
```

Use `attempt_03` and `attempt_04` for later rounds. The packet filenames may
restart at `packet_0001.*` inside each directory; no previous repair directory
needs to be deleted or overwritten. Each repair packet binds the public task,
candidate record, and generation attempt and sets `target_attempt` to the
previous attempt plus one. No repair packet is exported after attempt 4 or
after acceptance.

Repeat candidate validation with `--append`, select only the new attempt with
`--attempt`, prepare a new run directory, and ingest with `--append`. Once an
attempt is accepted, the workflow is terminal for that task and no further
repair packet is produced.

Reference RTL, testbench/support content, private paths, credentials, raw logs,
expected vectors, and private hashes never enter teacher-visible packets,
candidate records, generation attempts, or reports.
