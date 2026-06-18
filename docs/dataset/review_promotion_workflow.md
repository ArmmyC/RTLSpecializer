# Public draft review and promotion workflow

Imported public rows are not training labels. `import_public_dataset.py` creates structural draft rows so reviewers have complete `dataset_v0.1` envelopes, but the assistant answer is a conservative stub.

The local workflow is:

```text
local public artifacts
  -> import_public_dataset.py
  -> draft JSONL
  -> prepare_review_packet.py
  -> human or offline edits
  -> promote_reviewed_rows.py
  -> validated public JSONL
```

No step downloads public datasets, executes RTL, runs EDA tools, calls an external LLM, or proves correctness.

## Prepare a review packet

```bash
python scripts/dataset/prepare_review_packet.py \
  --input data/drafts/public_manifest_draft_v0.1.jsonl \
  --output-dir data/review/public_manifest_batch_001 \
  --json
```

The packet contains:

- `README.md` with reviewer instructions.
- `review_manifest.jsonl` with row IDs and file paths.
- `rows/<row-id>.review.md` with metadata, provenance, artifacts, current answer, checklist, and next action.
- `rows/<row-id>.json` with an exact JSON copy of the source row.

Packet generation does not mutate source rows or mark anything validated.

## Edit reviewed rows

Reviewers may edit JSONL directly or use an offline process to create a reviewed JSONL file. A promotable row must replace the import stub with a grounded answer:

- concrete issue visible in supplied artifacts,
- evidence naming signals or report fields,
- time reasoning covering clock/reset/latency/state risk,
- space reasoning covering area/activity resources and evidence limits,
- conservative claim levels unless real matching tool evidence exists,
- lint/compile and relevant verification checks in the plan,
- no private/proprietary content.

## Promote reviewed rows

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/public_manifest_batch_001/reviewed_rows.jsonl \
  --output data/processed/public_validated_v0.1.jsonl \
  --report data/reports/public_validated_v0.1_report.json \
  --json
```

By default promotion writes accepted rows with:

```text
review_status: validated
```

Use `--target-status reviewed` only when a human explicitly wants reviewed status. Promotion rejects unedited import stubs by default; `--allow-stub-answer` exists only for exceptional debugging and should not be used for training candidates.

Promotion writes:

- accepted output JSONL,
- `<output>.rejected.jsonl` with reason, errors, and original row,
- report JSON with counts by source, task type, design family, and rejection reason.

## Gates and limits

Promotion enforces public-source, license, and provenance gates because public availability does not imply training permission or correctness. Rows with `license: unknown`, `uncertain`, or `todo` are rejected. Rows missing `provenance.public_dataset_name` or `provenance.notes` are rejected.

Promotion also runs the existing dataset validator, so unsupported `verified` or `tool_supported` claims remain rejected. Passing promotion means the row is a validated candidate for later dataset splitting; it still does not prove RTL correctness, area improvement, activity improvement, or power behavior.
