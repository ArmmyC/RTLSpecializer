# Manual RTL teacher generation and verification workflow

This is an explicitly manual, local workflow. RTLSpecializer does not call a
model API, expose an endpoint, invoke a subprocess, execute RTL or testbenches,
invoke RTLBench, or invoke an EDA tool. The operator performs the LLM
interaction and isolated RTLBench run. Human review is required before any
dataset promotion.

Initialize one canonical run first:

```bash
export RUN_ID=pilot_001
export RUN_ROOT="data/runs/manual_rtl_teacher/${RUN_ID}"
export VERILOG_EVAL_ROOT="data/raw/verilog_eval/upstream/dataset_spec-to-rtl"

python scripts/dataset/init_manual_rtl_run.py \
  --run-id "$RUN_ID" \
  --source-dataset VerilogEval \
  --runs-root data/runs/manual_rtl_teacher \
  --json
```

## 1. Prepare and assemble tasks

```bash
python scripts/dataset/export_rtl_generation_normalization_batches.py \
  --input "$VERILOG_EVAL_ROOT" \
  --output-dir "$RUN_ROOT/normalization/packets" \
  --private-output-dir "$RUN_ROOT/private_assets" \
  --batch-size 5 \
  --limit 5 \
  --json
```

Send only the public normalization batch manually to the teacher. Save the
returned JSON under:

```text
$RUN_ROOT/normalization/responses/batch_001_response.json
```

Validate and assemble it without changing the existing CLI contracts:

```bash
python scripts/dataset/validate_rtl_generation_normalized_batch.py \
  --raw-batch "$RUN_ROOT/normalization/packets/batch_001.json" \
  --normalized "$RUN_ROOT/normalization/responses/batch_001_response.json" \
  --private-assets "$RUN_ROOT/private_assets/verification_assets.jsonl" \
  --json

python scripts/dataset/assemble_rtl_generation_inputs.py \
  --normalized "$RUN_ROOT/normalization/responses/batch_001_response.json" \
  --private-assets "$RUN_ROOT/private_assets/verification_assets.jsonl" \
  --tasks-output "$RUN_ROOT/tasks/generation_tasks.jsonl" \
  --assets-output "$RUN_ROOT/tasks/verification_assets.jsonl" \
  --json
```

Reference RTL and testbenches remain private. The task and verification-asset
manifests are not approval records.

## 2. Export teacher packets and validate returns

```bash
python scripts/dataset/export_rtl_teacher_generation_packets.py \
  --tasks "$RUN_ROOT/tasks/generation_tasks.jsonl" \
  --output-dir "$RUN_ROOT/teacher/packets" \
  --batch-size 1 \
  --limit 5 \
  --json
```

Copy `packet_0001.md` into the teacher manually. Save only its returned JSON as:
`$RUN_ROOT/teacher/responses/packet_0001_response.json`.

```bash
python scripts/dataset/validate_rtl_teacher_candidate_batch.py \
  --packet "$RUN_ROOT/teacher/packets/packet_0001.json" \
  --response "$RUN_ROOT/teacher/responses/packet_0001_response.json" \
  --private-assets "$RUN_ROOT/tasks/verification_assets.jsonl" \
  --private-assets-root "$RUN_ROOT/private_assets" \
  --output "$RUN_ROOT/teacher/candidate_records.jsonl" \
  --json
```

For repair responses, append to the same candidate-record file using the
repair packet and `--append`.

## 3. Prepare a verification handoff

```bash
python scripts/dataset/prepare_rtl_candidate_verification.py \
  --tasks "$RUN_ROOT/tasks/generation_tasks.jsonl" \
  --assets "$RUN_ROOT/tasks/verification_assets.jsonl" \
  --private-assets-root "$RUN_ROOT/private_assets" \
  --candidates "$RUN_ROOT/teacher/candidate_records.jsonl" \
  --attempt 1 \
  --output-dir "$RUN_ROOT/verification/attempt_01" \
  --json
```

Run the generated RTLBench command manually inside a disposable,
least-privileged container or VM with no network access or production secrets.
RTLSpecializer does not invoke it.

## 4. Ingest evidence

```bash
python scripts/dataset/ingest_rtl_candidate_evidence.py \
  --plan "$RUN_ROOT/verification/attempt_01/verification_plan.jsonl" \
  --evidence "$RUN_ROOT/verification/attempt_01/candidate_evidence.jsonl" \
  --output "$RUN_ROOT/verification/generation_attempts.jsonl" \
  --json
```

Evidence is append-only across attempts. A verification pass is not approval.

## 5. Export bounded repair packets

```bash
python scripts/dataset/export_rtl_teacher_repair_packets.py \
  --tasks "$RUN_ROOT/tasks/generation_tasks.jsonl" \
  --candidates "$RUN_ROOT/teacher/candidate_records.jsonl" \
  --attempts "$RUN_ROOT/verification/generation_attempts.jsonl" \
  --output-dir "$RUN_ROOT/repairs/attempt_02" \
  --max-attempts 4 \
  --batch-size 1 \
  --json
```

Use `attempt_03` and `attempt_04` for later rounds. No repair packet is
exported after attempt four or after acceptance. Teacher output is not reviewed
data, verified RTL is not automatically approved, and reviewed data is not
automatically a packaged distill dataset.
