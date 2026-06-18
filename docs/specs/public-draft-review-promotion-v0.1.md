# Feature Spec: Public Draft Review and Promotion v0.1

## 1. Goal

Build a local review and promotion workflow that turns imported public draft rows into validated dataset candidates without weakening the existing safety rules.

The current importer intentionally creates conservative draft rows:

```text
review_status: draft
split: unsplit
```

Those rows are structurally valid, but they are not training labels because their assistant answer is a generic import stub. This feature creates the next dataset step:

```text
imported draft JSONL
  -> review packet
  -> human or offline LLM edits outside this script
  -> promotion validator
  -> validated public JSONL
```

The goal is not to automate correctness. The goal is to make review, rejection, promotion, and auditability explicit and testable.

The promoted output should contain only rows that are ready to be considered for training or evaluation after normal splitting. It must reject generic import stubs, unsafe claims, missing provenance, duplicate IDs, unreviewed rows, and rows with unsupported `verified` or `tool_supported` labels.

## 2. Non-goals

Do not build:

- Model fine-tuning.
- Runtime inference.
- External LLM API calls.
- Web UI.
- Automatic public dataset download.
- EDA execution.
- Automatic correctness proof.
- Automatic promotion from draft to reviewed without edited content.
- New schema versions.
- Private/company RTL ingestion.

## 3. Assumptions

- The repo keeps `dataset_v0.1`, `rtl_task_v0.1`, and `rtl_answer_v0.1`.
- Imported public rows remain draft until a human or offline process edits them.
- The script does not call an LLM. It only prepares review artifacts and validates/promotes edited rows.
- Reviewers may edit JSONL directly or create a reviewed JSONL file using external tools.
- Promotion output should usually use `review_status: validated`, not `reviewed`, unless a human explicitly marks rows as reviewed.
- Existing `validate_dataset_file` remains the main validator.
- This feature should use Python standard library only.

## 4. User stories

- As a dataset reviewer, I want review packets for imported draft rows, so that I can inspect artifacts and rewrite generic answers into grounded RTL review labels.
- As a dataset maintainer, I want a promotion CLI, so that only non-stub, validated public rows enter the candidate dataset.
- As a future trainer, I want promoted rows to be clearly separated from raw drafts, so that training never accidentally consumes import stubs.
- As a project lead, I want promotion reports, so that I can see accepted, rejected, and risky rows before scaling.
- As a security-conscious contributor, I want all review and promotion to stay local and not execute artifacts.

## 5. UX / UI requirements

No graphical UI.

Add two CLI scripts under `scripts/dataset/`.

### 5.1 Prepare review packet

```bash
python scripts/dataset/prepare_review_packet.py \
  --input data/drafts/public_manifest_draft_v0.1.jsonl \
  --output-dir data/review/public_manifest_batch_001
```

Required outputs:

```text
data/review/public_manifest_batch_001/
  README.md
  review_manifest.jsonl
  rows/
    <row-id>.review.md
    <row-id>.json
```

Each Markdown review file must include:

- row ID,
- source,
- license,
- design family,
- task type,
- user goal,
- provenance,
- supplied artifacts in fenced code blocks,
- current assistant answer,
- checklist for reviewer decisions,
- explicit warning that imported answers are draft stubs.

### 5.2 Promote reviewed rows

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/public_manifest_batch_001/reviewed_rows.jsonl \
  --output data/processed/public_validated_v0.1.jsonl \
  --report data/reports/public_validated_v0.1_report.json
```

Supported options:

```text
--input <reviewed-jsonl>
--output <validated-jsonl>
--report <json-report-path>
--target-status validated|reviewed
--allow-stub-answer
--strict
--json
```

Default behavior:

- `--target-status validated`
- reject stub answers,
- reject unsafe or unsupported claims,
- reject draft/rejected rows,
- write accepted rows to output,
- write rejected rows to `<output>.rejected.jsonl`,
- write a machine-readable report.

### 5.3 Success state

```text
Promotion completed.

Input rows: 25
Accepted rows: 21
Rejected rows: 4
Output: data/processed/public_validated_v0.1.jsonl
Rejected output: data/processed/public_validated_v0.1.rejected.jsonl
Report: data/reports/public_validated_v0.1_report.json
```

Exit code: `0` if at least one row is accepted and no strict failure applies.

### 5.4 Failure state

```text
Promotion failed.

Errors:
- no rows accepted
```

Exit code: `1`.

### 5.5 JSON output

When `--json` is set, output:

```json
{
  "ok": true,
  "input_rows": 25,
  "accepted_rows": 21,
  "rejected_rows": 4,
  "output": "data/processed/public_validated_v0.1.jsonl",
  "rejected_output": "data/processed/public_validated_v0.1.rejected.jsonl",
  "report": "data/reports/public_validated_v0.1_report.json",
  "errors": [],
  "warnings": []
}
```

## 6. Functional requirements

### FR-1: Add review packet generator

Create:

```text
scripts/dataset/prepare_review_packet.py
```

It must:

- read a JSONL dataset file,
- validate it structurally using `validate_dataset_file`,
- accept draft rows as input,
- create the output directory,
- write one JSON file per row,
- write one Markdown review file per row,
- write `review_manifest.jsonl` listing row ID, source, license, task type, design family, and paths to review files,
- write a `README.md` explaining the review workflow.

### FR-2: Review packet must not mutate source rows

The review packet generator must not change the input row content. It may copy each row to `<row-id>.json`, but it must not mark rows as reviewed or validated.

### FR-3: Markdown review content

Each `<row-id>.review.md` must include:

- metadata table,
- provenance section,
- artifacts section,
- current answer section,
- review checklist,
- recommended next action section.

Review checklist:

```text
[ ] Issue is visible in supplied artifact.
[ ] Evidence names concrete signals or report fields.
[ ] Time reasoning addresses clock/reset/latency/state risk.
[ ] Space reasoning addresses area/activity resources.
[ ] Claim levels match available evidence.
[ ] Verification plan includes lint/compile and relevant checks.
[ ] No power claim without power report.
[ ] No private/proprietary data included.
```

### FR-4: Add promotion CLI

Create:

```text
scripts/dataset/promote_reviewed_rows.py
```

It must:

- read a reviewed JSONL file,
- validate every row using existing validation,
- enforce public-promotion-specific quality gates,
- write accepted rows to output,
- write rejected rows to a sidecar rejected JSONL,
- write a report JSON,
- return clear exit codes.

### FR-5: Promotion target status

Promotion must set or require the target review status.

Default:

```text
review_status: validated
```

Rules:

- Input rows with `review_status: draft` may be promoted only after passing all promotion gates. The output row must be written with `review_status` set to the target status.
- Input rows with `review_status: rejected` must always be rejected.
- Input rows with `review_status: validated` or `reviewed` may pass through if they satisfy all gates.
- `--target-status reviewed` is allowed only when the user explicitly passes it.

### FR-6: Reject generic import stubs by default

Promotion must reject rows whose assistant answer still looks like an import stub.

Reject if answer text contains any of these phrases, unless `--allow-stub-answer` is passed:

```text
Imported public dataset draft row requires review
Treat this as a draft review seed only
No optimization effect is claimed
Imported public artifacts may be incomplete
```

Also reject if:

- `issue_summary[0].severity == "low"` and issue is generic import text,
- all claim levels are `insufficient_evidence` and task is not a pure report/indexing row,
- `safe_optimization.patch_style == "explanation_only"` for a task that should contain actual review judgment, unless the task is `rtl_tool_report_explanation`.

### FR-7: Enforce grounded answer quality for promoted public rows

For promoted rows whose source starts with `public_` or equals `llm_converted_public`:

- `issue_summary` must be non-empty.
- Each issue must have non-empty `evidence.reason`.
- If RTL artifacts are present, `evidence.signal_names` must include at least one concrete signal or artifact-specific name.
- `time_reasoning.clock_cycle_behavior` must not be generic placeholder text.
- `space_reasoning.area_risk` and `space_reasoning.activity_risk` must mention evidence limitations or tool requirements.
- `verification_plan` must include lint/compile.
- Area/activity tasks must mention synthesis and VCD/toggle checks.

### FR-8: Enforce provenance and license gates

Promotion must reject rows when:

- `license` is empty,
- `license` equals `unknown`, `uncertain`, or `todo`, case-insensitive,
- `provenance.public_dataset_name` is missing or empty,
- `provenance.notes` is missing or empty,
- `source` is not a public source enum or `llm_converted_public`.

Allowed public sources:

```text
public_verilog_eval
public_rtllm
public_rtllm_2
public_rtlfixer
public_openllm_rtl
llm_converted_public
```

### FR-9: Validate output rows after writing

Promotion must validate:

- each accepted row individually before writing,
- the full accepted output file after writing.

If full-output validation fails, the CLI must fail and include errors in the report.

### FR-10: Rejected sidecar format

Rejected sidecar rows must include:

```json
{
  "id": "public_verilog_eval_counter_001",
  "reason": "stub answer",
  "errors": ["issue_summary still contains imported draft stub text"],
  "row": { }
}
```

### FR-11: Report JSON

The report must include:

```json
{
  "ok": true,
  "input_rows": 25,
  "accepted_rows": 21,
  "rejected_rows": 4,
  "by_source": {},
  "by_task_type": {},
  "by_design_family": {},
  "rejection_reasons": {},
  "output": "...",
  "rejected_output": "..."
}
```

### FR-12: Add tests

Add tests under `tests/dataset/` for:

- review packet generation creates README, manifest, markdown, and JSON row copy,
- review packet does not mutate rows,
- promotion rejects unedited import stubs,
- promotion accepts a manually edited valid public row and changes status to `validated`,
- promotion rejects uncertain license,
- promotion rejects missing public dataset provenance,
- promotion rejects private/non-public source,
- promotion writes rejected sidecar with reason and row,
- promotion writes report JSON with counts,
- promoted output validates under `validate_dataset_file --strict`,
- CLI `--json` output is parseable.

### FR-13: Add fixture for edited reviewed row

Create a synthetic fixture:

```text
tests/fixtures/public_review/
  draft_rows.jsonl
  reviewed_rows.jsonl
```

`draft_rows.jsonl` may be generated from the public manifest fixture or committed as a small synthetic fixture.

`reviewed_rows.jsonl` must contain a non-stub answer with:

- concrete issue,
- concrete evidence,
- conservative claim levels,
- functional risk,
- verification plan,
- public provenance,
- non-uncertain license.

### FR-14: Update documentation

Update or create:

```text
docs/dataset/review_promotion_workflow.md
docs/dataset/dataset_guidelines.md
docs/dataset/public_dataset_sources.md
README.md
```

Docs must explain:

- imported rows are not training labels,
- how to generate a review packet,
- how to edit reviewed rows,
- how to promote rows,
- how rejected rows are reported,
- why promotion does not prove correctness,
- why public-source license/provenance gates exist.

## 7. Technical requirements

### 7.1 Architecture

Add a small promotion layer after import:

```text
import_public_dataset.py
  -> data/drafts/*.jsonl
  -> prepare_review_packet.py
  -> human/offline edits
  -> promote_reviewed_rows.py
  -> data/processed/public_validated_v0.1.jsonl
```

Suggested reusable module:

```text
scripts/dataset/review_promotion.py
```

CLI scripts should be thin wrappers around reusable functions.

### 7.2 Quality gate implementation

Use simple deterministic checks. Do not try to infer real RTL correctness.

Suggested functions:

```python
def is_stub_answer(answer: dict) -> bool:
    ...

def public_promotion_errors(row: dict, allow_stub_answer: bool = False) -> list[str]:
    ...

def promote_rows(rows: list[dict], target_status: str) -> PromotionResult:
    ...
```

### 7.3 Security

- Do not execute artifacts.
- Do not shell out.
- Do not call external services.
- Do not download public datasets.
- Do not include private/company RTL.
- Treat reviewed JSONL as untrusted input.

### 7.4 Dependencies

Use standard library only.

Allowed modules include:

```text
argparse
json
pathlib
dataclasses
typing
collections
copy
re
sys
```

## 8. Files likely involved

Create:

```text
scripts/dataset/prepare_review_packet.py
scripts/dataset/promote_reviewed_rows.py
scripts/dataset/review_promotion.py
docs/dataset/review_promotion_workflow.md
tests/dataset/test_review_promotion.py
tests/fixtures/public_review/draft_rows.jsonl
tests/fixtures/public_review/reviewed_rows.jsonl
```

Modify:

```text
docs/dataset/dataset_guidelines.md
docs/dataset/public_dataset_sources.md
README.md
```

Do not modify unrelated files.

## 9. Data model

No database and no schema migration.

### Review manifest row

```json
{
  "id": "public_verilog_eval_counter_001",
  "source": "public_verilog_eval",
  "license": "MIT",
  "task_type": "rtl_bug_review",
  "design_family": "counter",
  "review_markdown": "rows/public_verilog_eval_counter_001.review.md",
  "row_json": "rows/public_verilog_eval_counter_001.json"
}
```

### Promotion report

See FR-11.

### Promoted dataset row

Same `dataset_v0.1` row format. Promotion changes only fields needed for status normalization unless the input reviewed row already contains edited content.

## 10. API contract

### Prepare Review Packet

- Name: Prepare Review Packet
- Method: CLI
- Path: `scripts/dataset/prepare_review_packet.py`

Request:

```bash
python scripts/dataset/prepare_review_packet.py \
  --input data/drafts/public_manifest_draft_v0.1.jsonl \
  --output-dir data/review/public_manifest_batch_001 \
  --json
```

Response:

```json
{
  "ok": true,
  "input_rows": 10,
  "packet_rows": 10,
  "output_dir": "data/review/public_manifest_batch_001",
  "manifest": "data/review/public_manifest_batch_001/review_manifest.jsonl",
  "errors": [],
  "warnings": []
}
```

### Promote Reviewed Rows

- Name: Promote Reviewed Rows
- Method: CLI
- Path: `scripts/dataset/promote_reviewed_rows.py`

Request:

```bash
python scripts/dataset/promote_reviewed_rows.py \
  --input data/review/public_manifest_batch_001/reviewed_rows.jsonl \
  --output data/processed/public_validated_v0.1.jsonl \
  --report data/reports/public_validated_v0.1_report.json \
  --json
```

Response:

```json
{
  "ok": true,
  "input_rows": 10,
  "accepted_rows": 8,
  "rejected_rows": 2,
  "output": "data/processed/public_validated_v0.1.jsonl",
  "rejected_output": "data/processed/public_validated_v0.1.rejected.jsonl",
  "report": "data/reports/public_validated_v0.1_report.json",
  "errors": [],
  "warnings": []
}
```

Error cases:

- input missing,
- malformed JSONL,
- no rows accepted,
- invalid target status,
- output validation fails,
- all rows are stubs,
- license/provenance gate fails,
- unsafe claim validation fails.

## 11. Edge cases

Handle:

- empty input file,
- malformed JSONL,
- duplicate row IDs,
- draft row with stub answer,
- draft row with edited valid answer,
- reviewed row that still has import stub text,
- row with `review_status: rejected`,
- row with private source,
- row with `license: unknown`,
- row with missing provenance dataset name,
- row with unsupported power claim,
- row with `verified` claim but no pass evidence,
- row with no RTL but report artifact,
- existing output directory for review packet,
- existing output file for promotion,
- Windows path separators.

## 12. Testing plan

### Unit tests

Test:

- `is_stub_answer`,
- provenance gate,
- license gate,
- public source gate,
- target status normalization,
- promotion rejection reasons.

### Integration tests

Run:

```bash
python scripts/dataset/prepare_review_packet.py \
  --input tests/fixtures/public_review/draft_rows.jsonl \
  --output-dir <tmpdir>/review \
  --json

python scripts/dataset/promote_reviewed_rows.py \
  --input tests/fixtures/public_review/reviewed_rows.jsonl \
  --output <tmpdir>/public_validated_v0.1.jsonl \
  --report <tmpdir>/public_validated_report.json \
  --json

python scripts/dataset/validate_dataset.py \
  --input <tmpdir>/public_validated_v0.1.jsonl \
  --strict
```

### Manual checks

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

## 13. Definition of done

The task is done only when:

- Review packet generation works for draft JSONL.
- Promotion rejects unedited import stubs by default.
- Promotion accepts a valid edited public row and writes `review_status: validated` by default.
- Promotion rejects uncertain license and missing provenance.
- Promotion rejects unsupported claims through existing validation.
- Promotion writes accepted output, rejected sidecar, and report JSON.
- Promoted output validates with `--strict`.
- Tests cover review packet and promotion behavior.
- Docs explain the workflow.
- No external services, downloads, EDA execution, or private data are introduced.

## 14. Codex implementation instructions

Implement this spec exactly.

Focus only on review packet generation and promotion of edited public draft rows.

Do not add training, inference, external LLM calls, public dataset downloads, EDA execution, or schema version changes.

Use standard-library Python only.

Keep the existing importer, validator, golden dataset, and split behavior compatible.

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run the new review packet and promotion CLIs against the test fixtures.

After finishing, commit and push. Summarize changed files, commands run, test results, and tradeoffs.