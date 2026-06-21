# Manual review session guide

Use this guide for a focused 60–90 minute human review session. The goal is to turn selected draft rows into genuinely reviewed rows that can pass the readiness gate—not to maximize row counts.

## Goal of the session

Review each intended VerilogEval row yourself, replace its draft answer with a correct and conservative answer, and leave it ready for strict readiness checking. Human judgment remains the approval boundary.

## Inputs

Work from these local files:

```text
data/review/verilog_eval_batch_001/selected_rows.jsonl
data/review/verilog_eval_batch_001/reviewed_rows.jsonl
data/review/verilog_eval_batch_001/review_packet/
```

These are generated/local review artifacts and normally must not be committed.

## Suggested session shape

- 5–10 minutes: open the packet and choose the rows to finish.
- 45–70 minutes: review and edit one row at a time.
- 10 minutes: run readiness, inspect failures, and record follow-up work.

## What the human reviewer does

- Open each `.review.md` packet and understand the task prompt and specification.
- Inspect the supplied RTL and testbench artifacts as text; do not execute them as part of review.
- Write or edit that row's answer in `reviewed_rows.jsonl`.
- Avoid unsupported correctness, area, power, or activity claims.
- Include a practical verification plan tied to the task and available artifacts.
- Mark only rows you genuinely reviewed as ready for readiness checking.

Use the [RTL answer review checklist](rtl_answer_review_checklist.md) while editing each answer.

## What not to do

- Do not use Codex or another model to silently fill reviewed answers.
- Do not copy reference RTL as an answer unless the task truly asks for it and licensing/provenance permit that use.
- Do not claim simulation, synthesis, equivalence, toggle, or power evidence unless that evidence exists in the row.
- Do not edit IDs to make gates pass.
- Do not promote rows merely to increase counts.

## Suggested per-row workflow

1. Read the task and user goal.
2. Read the supplied artifacts.
3. Decide the intended answer shape.
4. Edit the reviewed answer.
5. Check every claim level against the available evidence.
6. Add a practical verification plan.
7. Save the row without changing its ID.
8. Move to the next row.

## After editing

Run the read-only readiness check:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --strict \
  --json
```

Fix failures by improving the reviewed row, then rerun the check. Do not weaken gates or alter identifiers to force a pass.

## Done condition

The session is done only when:

- every intended row has a real, human-reviewed answer;
- strict readiness passes;
- license and provenance have been checked before promotion or finalization;
- generated review outputs remain uncommitted.
