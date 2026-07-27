# Manual RTL teacher generation and verification workflow

This is a manual, local workflow. No model API is used. RTLSpecializer never
executes RTL, testbenches, RTLBench, Icarus, VVP, Verilator, Yosys, or another
EDA tool. Reference RTL and private testbenches are never sent to the LLM.
Generated candidates and evidence remain local, and even accepted results need
human review before any dataset promotion.

## Initial packet

```bash
python scripts/dataset/export_rtl_teacher_generation_packets.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --output-dir data/review/rtl_teacher_generation_packets \
  --batch-size 1 --limit 5 --json
```

Copy `packet_0001.md` into the chosen LLM. Save only its returned JSON as
`packet_0001_response.json`. The response must contain exactly `rows`; do not
save Markdown fences or explanatory prose.

## Validate the response

```bash
python scripts/dataset/validate_rtl_teacher_candidate_batch.py \
  --packet data/review/rtl_teacher_generation_packets/packet_0001.json \
  --response data/.local_data/manual_teacher_responses/packet_0001_response.json \
  --private-assets data/review/rtl_generation_pilot/verification_assets.jsonl \
  --private-assets-root data/.local_data/rtl_generation_verification_assets \
  --output data/.local_data/validated_teacher_candidates/candidates.jsonl \
  --json
```

The validator derives the candidate ID and attempt locally, verifies the
candidate hash, checks the declared top-module text conservatively, and checks
for private asset contamination. It does not compile or simulate.

## Prepare the RTLBench handoff

```bash
python scripts/dataset/prepare_rtl_candidate_verification.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --assets data/review/rtl_generation_pilot/verification_assets.jsonl \
  --private-assets-root data/.local_data/rtl_generation_verification_assets \
  --candidates data/.local_data/validated_teacher_candidates/candidates.jsonl \
  --output-dir data/.local_data/rtl_candidate_verification/run_001 \
  --json
```

The output contains `candidate_manifest.jsonl`, `verification_plan.jsonl`,
`run_instructions.md`, and a workspace with only candidate RTL, testbench, and
support files. Reference RTL is never copied.

## Human verification

Run the exact command in `run_instructions.md` manually inside a disposable,
least-privileged container or VM with no network access or production secrets:

```bash
rtlbench verify-candidates \
  --manifest <output-dir>/candidate_manifest.jsonl \
  --output <output-dir>/candidate_evidence.jsonl \
  --workspace-root <output-dir>/workspace \
  --work-dir /tmp/rtlbench-candidate-work \
  --force
```

Externally enforce CPU, memory, process, output, disk, and time limits.
RTLSpecializer does not invoke this command.

## Ingest evidence

```bash
python scripts/dataset/ingest_rtl_candidate_evidence.py \
  --plan data/.local_data/rtl_candidate_verification/run_001/verification_plan.jsonl \
  --evidence data/.local_data/rtl_candidate_verification/run_001/candidate_evidence.jsonl \
  --output data/review/rtl_generation_pilot/generation_attempts.jsonl \
  --json
```

Ingestion checks identities, hashes, requested checks, check leaves, mismatch
reports, acceptance consistency, failure-category priority, diagnostics, and
private leakage. A simulation pass is evidence for this candidate under this
testbench contract; it is not a proof of equivalence or a golden-data approval.

## Failed candidate and repair

```bash
python scripts/dataset/export_rtl_teacher_repair_packets.py \
  --tasks data/review/rtl_generation_pilot/generation_tasks.jsonl \
  --candidates data/.local_data/validated_teacher_candidates/candidates.jsonl \
  --attempts data/review/rtl_generation_pilot/generation_attempts.jsonl \
  --output-dir data/review/rtl_teacher_repair_packets \
  --max-attempts 4 --batch-size 1 --json
```

Only the latest failed attempt below attempt four is exported. The repair packet
contains the original public task, complete previous candidate, and bounded
sanitized evidence. It excludes reference RTL, testbench/support content,
expected vectors, hashes, private paths, raw logs, and API details. Copy its
Markdown packet to the LLM, save JSON only, and validate it with the same
candidate validator. Repeat at most four total attempts; no packet is exported
for an accepted or attempt-four candidate.
