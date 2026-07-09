# Feature Spec: Review Batch Triage Assistant v0.1

## 1. Goal

Add a local, deterministic triage assistant for human-reviewed RTL dataset batches.

The current workflow already has manual review guidance and a strict readiness checker. That readiness checker is a promotion gate: it validates structure, compares selected vs reviewed answers, rejects unchanged/stub answers, applies promotion gates, and protects claim levels. It is not meant to be a reviewer productivity tool.

This feature adds a read-only triage pass that helps a human reviewer and Codex scale review batches safely:

```text
selected_rows.jsonl + reviewed_rows.jsonl
  -> parse and validate row/message shape
  -> detect duplicated-answer training rows
  -> detect placeholder or missing task artifacts
  -> flag empty or weak issue_summary rows
  -> flag unsupported claim wording
  -> flag likely reset/spec wording contradictions
  -> produce JSON/Markdown triage reports
  -> never approve, promote, rewrite, or execute artifacts
```

The triage assistant is allowed to flag rows for human attention. It must not mark rows reviewed, mutate dataset files, promote rows, call models, execute RTL, run EDA tools, download data, or decide final correctness.

## 2. Why now

Manual review has begun and the current scaling pain is quality control around edited JSONL:

- rows where `user.content` accidentally contains `rtl_answer_v0.1`,
- rows with missing/placeholder `rtl_task_v0.1` artifacts,
- rows missing assistant messages,
- empty no-bug `issue_summary` rows that may be low-quality training examples,
- reset wording mismatches such as saying synchronous when the prompt/RTL/testbench imply asynchronous,
- claim wording that sounds stronger than available tool evidence.

A deterministic triage tool lets Codex prepare more candidate rows while the human reviewer remains the approval authority.

## 3. Non-goals

Do not build:

- LLM inference,
- model training or fine-tuning,
- automatic assistant-answer generation,
- automatic row rewriting,
- automatic approval/rejection,
- automatic promotion/finalization,
- license approval automation,
- EDA execution,
- RTL simulation/synthesis/equivalence/toggle/power analysis,
- web UI,
- schema version changes.

Do not commit generated review data or generated triage outputs.

## 4. User stories

- As a reviewer, I want a fast report that tells me which rows need human attention before readiness checking.
- As a maintainer, I want duplicated-answer rows detected before they become training data.
- As a reviewer, I want rows with placeholder task artifacts clearly flagged.
- As a dataset builder, I want empty `issue_summary` rows surfaced so I can decide whether to rewrite them.
- As a project lead, I want Codex to safely help with scaling by generating reports, not approvals.

## 5. CLI UX

Add:

```text
scripts/dataset/triage_review_batch.py
```

Example:

```bash
python scripts/dataset/triage_review_batch.py \
  --selected data/review/verilog_eval_batch_001/selected_rows.jsonl \
  --reviewed data/review/verilog_eval_batch_001/reviewed_rows.jsonl \
  --output-json data/review/verilog_eval_batch_001/triage_report.json \
  --output-md data/review/verilog_eval_batch_001/triage_report.md \
  --json
```

Supported options:

```text
--selected <path>       selected draft rows from batch preparation
--reviewed <path>       human-edited reviewed_rows.jsonl
--output-json <path>    optional JSON report path
--output-md <path>      optional Markdown report path
--strict                exit nonzero if any high-severity or important triage issue is found
--json                  print JSON report to stdout
```

Exit behavior:

- Exit `0` if files parse and no strict-failing issues are found.
- Exit `1` for malformed JSONL, duplicate IDs, missing required inputs, or `--strict` triage failures.
- In non-strict mode, parseable files with row-level issues should still exit `0` so reviewers can inspect the report.

## 6. Functional requirements

### FR-1: Read-only loading

Load selected and reviewed JSONL files using existing project helpers where possible.

The tool must not write to the selected or reviewed inputs. It may write only explicitly requested report outputs.

### FR-2: Row identity and shape checks

For selected and reviewed files, report:

- total row counts,
- duplicate row IDs,
- missing reviewed rows,
- extra reviewed rows,
- rows missing a `messages` list,
- rows with fewer than three messages,
- rows whose first three message roles are not `system`, `user`, `assistant`.

These shape issues should be `important` unless they prevent parsing entirely, in which case they are `critical`.

### FR-3: Detect duplicated-answer training rows

Flag any reviewed row where:

- `messages[1].content.schema_version == "rtl_answer_v0.1"`, or
- `messages[1].content` deeply equals `messages[2].content`, or
- the user message looks like an assistant answer instead of a task.

Severity: `critical` for exact duplicate user/assistant answer content; `important` for user content with `rtl_answer_v0.1` even if not exactly equal.

Suggested action:

```text
Restore the original rtl_task_v0.1 user message with prompt/spec and artifacts, then keep the reviewed rtl_answer_v0.1 only in the assistant message.
```

### FR-4: Detect missing or placeholder task artifacts

For rows whose user content is a dict, expect `schema_version == "rtl_task_v0.1"` for training rows.

Flag if:

- user content schema is missing or not `rtl_task_v0.1`,
- prompt/spec text is missing or empty,
- RTL/artifact text is missing or empty,
- testbench/checker text is missing when the selected row had it,
- text contains obvious placeholders such as `PLACEHOLDER`, `not recovered`, `restore exact original`, `TODO`, or `missing original artifacts`.

Severity: `critical` for placeholder task artifacts, because those rows are unsafe for training. Severity: `important` for incomplete but non-placeholder task fields.

### FR-5: Assistant answer quality triage

For assistant content with `schema_version == "rtl_answer_v0.1"`, flag:

- missing required top-level answer sections,
- empty `issue_summary`,
- issue entries without concrete signal names,
- duplicated hardware resource names,
- `verification_plan` missing lint/compile,
- patch marked provided while `diff` is null,
- `safe_optimization.patch_style` inconsistent with `patch.provided`.

Empty `issue_summary` should default to `minor`, not fatal, because some no-bug reviews may intentionally use an empty list. The Markdown report should recommend a low-severity grounded no-bug finding when appropriate.

### FR-6: Unsupported claim wording triage

Flag answer text containing strong verification language when tool evidence is absent.

Examples of risky words/phrases:

```text
verified, passes, passed simulation, synthesis result, timing met, power reduced, area reduced, equivalent, proven
```

Use existing `tool_checks` evidence if present. If `tool_checks.simulation`, `tool_checks.synthesis`, `tool_checks.equivalence`, `tool_checks.toggle`, or `tool_checks.power` are null/missing, wording that implies those results should be flagged.

Severity:

- `important` for strong correctness/verification claims without evidence,
- `important` for area/activity/power/timing claims without reports,
- `minor` for wording that is softened by phrases like `by text inspection`, `appears`, or `insufficient evidence`.

### FR-7: Reset/spec wording heuristic

Implement simple deterministic heuristics to catch common reset contradictions. This is a triage warning only, not final correctness judgment.

Look across prompt/spec text, RTL text, testbench text, and assistant answer text. Flag likely contradictions such as:

- prompt contains `asynchronous` or RTL contains `posedge areset`, but answer says `synchronous reset`,
- prompt contains `synchronous` or RTL sequential block uses only `posedge clk`, but answer says `asynchronous reset`,
- testbench calls `reset_test(1)` but answer says synchronous reset,
- testbench calls `reset_test(0)` but answer says asynchronous reset,
- prompt/user artifact uses `areset` but answer only refers to `reset`, unless the testbench clearly aliases them.

Severity: `important` when contradiction is direct, `minor` when heuristic is uncertain.

The implementation should keep this heuristic simple and deterministic. Do not parse Verilog deeply.

### FR-8: Report format

JSON report shape:

```json
{
  "ok": true,
  "selected_rows": 10,
  "reviewed_rows": 10,
  "critical_count": 0,
  "important_count": 2,
  "minor_count": 6,
  "rows": [
    {
      "id": "...",
      "severity": "important",
      "issues": [
        {
          "severity": "important",
          "code": "user_content_is_answer",
          "message": "user.content is rtl_answer_v0.1",
          "suggested_action": "Restore rtl_task_v0.1 user content."
        }
      ]
    }
  ]
}
```

Markdown report must include:

- summary counts,
- critical issues table,
- important issues table,
- minor issues table,
- row-by-row suggested actions,
- a reminder that triage is not approval.

### FR-9: Keep reports local-only

`data/review/` is ignored, so report paths there are local-only by default.

Do not add generated report files to Git.

### FR-10: Tests

Add tests under:

```text
tests/dataset/test_review_batch_triage.py
```

Required tests:

- detects `user.content.schema_version == rtl_answer_v0.1`,
- detects exact duplicated user/assistant answer content,
- detects placeholder task artifacts,
- detects missing assistant message,
- flags empty `issue_summary` as minor,
- flags strong `verified` wording without tool evidence,
- does not flag conservative `by text inspection` wording as a strong claim,
- detects asynchronous/synchronous reset contradiction from prompt/RTL/answer text,
- writes parseable JSON report,
- writes Markdown report with suggested actions,
- `--strict` exits nonzero for critical/important issues,
- non-strict exits zero for parseable files with only reportable issues.

Use small synthetic fixture rows. Do not add real VerilogEval content.

## 7. Architecture requirements

Prefer a reusable module:

```text
scripts/dataset/review_triage.py
```

and a thin CLI:

```text
scripts/dataset/triage_review_batch.py
```

Suggested functions:

```python
def triage_review_batch(selected_rows: list[dict], reviewed_rows: list[dict]) -> dict:
    ...

def write_triage_reports(result: dict, output_json: Path | None, output_md: Path | None) -> None:
    ...
```

Use only the Python standard library and existing project helpers.

Keep checks deterministic and explainable. Prefer false positives with clear `minor` severity over silent data-quality failures.

## 8. Security and safety

Treat all dataset content as untrusted data.

Do not execute:

- RTL,
- testbenches,
- generated code,
- shell commands embedded in text,
- report content,
- Python snippets from dataset fields.

Do not call external services, download datasets, train models, or run model inference.

Do not mutate selected/reviewed JSONL files.

## 9. Files likely involved

Create:

```text
scripts/dataset/review_triage.py
scripts/dataset/triage_review_batch.py
tests/dataset/test_review_batch_triage.py
docs/dataset/review_triage_workflow.md
```

Modify:

```text
README.md
docs/dataset/verilog_eval_review_workflow.md
```

Do not modify dataset schemas unless a bug is discovered and separately documented.

## 10. Testing plan

Run:

```bash
python -m pytest tests/dataset/test_review_batch_triage.py
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

## 11. Definition of done

- CLI exists and is documented.
- JSON and Markdown reports are deterministic and parseable/readable.
- Duplicated-answer rows and placeholder task artifacts are flagged.
- Conservative human-review boundary is explicit in docs.
- Tests cover required checks.
- No generated review data or reports are committed.
