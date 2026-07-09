# Process Spec: Manual Review Session Guide v0.1

## 1. Goal

Add a small manual-review guide and answer checklist so the next milestone is reviewing the first VerilogEval batch rows, not adding more infrastructure.

This is a human workflow aid. It must not ask Codex or any model to perform the review. The user is responsible for deciding whether each row is correct, conservative, and acceptable to promote.

## 2. Non-goals

Do not add:

- new Python source code,
- automatic review,
- model-generated reviewed answers,
- LLM calls,
- EDA tools,
- RTL simulation/synthesis/equivalence/toggle/power analysis,
- dataset schema changes,
- generated reviewed rows,
- committed review outputs,
- benchmark results.

## 3. Files to add

Create:

```text
docs/dataset/manual_review_session_guide.md
docs/dataset/rtl_answer_review_checklist.md
```

Update:

```text
README.md
docs/dataset/verilog_eval_review_workflow.md
```

Keep changes docs-only.

## 4. Manual review session guide

Create `docs/dataset/manual_review_session_guide.md`.

It should be short, practical, and organized for a 60-90 minute review session.

Required sections:

### 4.1 Goal of the session

Explain that the goal is to turn selected draft rows into manually reviewed rows that can pass readiness.

### 4.2 Inputs

List expected local files:

```text
data/review/verilog_eval_batch_001/selected_rows.jsonl
data/review/verilog_eval_batch_001/reviewed_rows.jsonl
data/review/verilog_eval_batch_001/review_packet/
```

Mention these files are local/generated and normally not committed.

### 4.3 What the human reviewer does

Explain that the reviewer should:

- open each `.review.md` packet,
- understand the task prompt/spec,
- inspect RTL/testbench artifacts as text,
- write or edit the answer in `reviewed_rows.jsonl`,
- avoid unsupported correctness, area, power, or activity claims,
- include a practical verification plan,
- mark only genuinely reviewed rows as ready for readiness checking.

### 4.4 What not to do

State clearly:

- do not use Codex to silently fill reviewed answers,
- do not copy reference RTL as an answer unless the task truly asks for it and licensing/provenance allow it,
- do not claim simulation/synthesis/equivalence/toggle/power evidence unless such evidence exists in the row,
- do not edit IDs to make gates pass,
- do not promote rows just to increase counts.

### 4.5 Suggested per-row workflow

Include a short checklist:

1. Read task/user goal.
2. Read artifacts.
3. Decide the intended answer shape.
4. Edit the reviewed answer.
5. Check claim levels.
6. Add verification plan.
7. Save.
8. Move to the next row.

### 4.6 After editing

Include the readiness command:

```bash
python scripts/dataset/check_review_batch_readiness.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/readiness_report.json \
  --output-md data/review/verilog_eval_batch_001/readiness_report.md \
  --strict \
  --json
```

Explain that failures should be fixed by improving the reviewed row, not by weakening gates.

### 4.7 Done condition

Define done as:

- every intended row has a real reviewed answer,
- readiness passes strictly,
- license/provenance has been checked before promotion/finalization,
- generated review outputs remain uncommitted.

## 5. RTL answer review checklist

Create `docs/dataset/rtl_answer_review_checklist.md`.

It should be a reusable checklist for reviewing one `rtl_answer_v0.1` answer.

Required checklist items:

- schema version is correct,
- task type matches the row,
- answer addresses the actual prompt,
- no placeholder/stub phrasing remains,
- implementation explanation is specific enough to be useful,
- verification plan includes lint/compile and focused simulation where appropriate,
- correctness claim is not `verified` without evidence,
- area/activity/power claims are `insufficient_evidence` unless relevant evidence exists,
- limitations are stated when evidence is missing,
- no private/proprietary text was added,
- no generated/raw local outputs are referenced as public evidence,
- answer is ready for readiness checking.

Include a tiny example of conservative claim language, but do not include long example answers.

## 6. README and workflow docs

Update README with a short link to the manual review guide.

Update `docs/dataset/verilog_eval_review_workflow.md` with a short note pointing reviewers to:

```text
docs/dataset/manual_review_session_guide.md
docs/dataset/rtl_answer_review_checklist.md
```

Do not duplicate the full guide there.

## 7. Validation

Run:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python -m pytest tests/dataset tests/eval
```

No real model calls, EDA calls, downloads, or generated review outputs should be created by this docs task.

## 8. Definition of done

Done only when:

- manual review session guide exists,
- RTL answer review checklist exists,
- README links to the guide,
- VerilogEval workflow docs link to the guide/checklist,
- no source code is changed,
- no generated data is committed,
- validation commands pass or any failure is clearly reported.

## 9. Codex implementation instructions

Implement this spec exactly.

This is a docs-only process task to support human review. Do not generate or edit reviewed dataset rows.

After finishing, commit and push. Summarize changed files, commands run, validation results, and the manual steps still required from the user.
