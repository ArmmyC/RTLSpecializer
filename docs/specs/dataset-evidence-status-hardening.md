# Feature Spec: Dataset Evidence Status Hardening

## 1. Goal

Tighten dataset claim-level validation so `tool_supported` and `verified` cannot be accepted merely because a tool-check object exists.

The current validator correctly requires evidence objects for claim levels, but it treats any non-null tool check as sufficient evidence. That means a row could claim `verified` correctness with a simulation check whose status is `unknown` or `fail`. This task closes that safety gap before dataset expansion or public conversion.

The goal is to make evidence semantics explicit and testable:

- `verified` requires an appropriate tool check with `status: "pass"`.
- `tool_supported` requires an appropriate supplied tool/report artifact or tool check with a meaningful status.
- `insufficient_evidence`, `suggestion_only`, and `not_applicable` remain allowed without tool checks.
- Golden report rows should not claim area support when the report explicitly says area units are unavailable.

## 2. Non-goals

Do not build:

- Model training.
- Public dataset downloading or conversion.
- External LLM calls.
- EDA tool execution.
- Power report support beyond existing metadata fields.
- A new schema version.
- A new dataset format.

## 3. Assumptions

- Keep `dataset_v0.1`, `rtl_task_v0.1`, and `rtl_answer_v0.1`.
- Keep the existing `tool_checks` object shape.
- Keep standard-library-only Python.
- Some report-explanation rows may use synthetic report excerpts, but their claim levels must stay conservative.
- `tool_supported` and `verified` are different: `verified` is much stricter.

## 4. User stories

- As a dataset maintainer, I want `verified` to require passing verification evidence, so that training rows do not teach unsupported certainty.
- As a reviewer, I want report-backed examples to distinguish report explanation from measured improvement, so that area/activity claims remain precise.
- As a future public dataset converter, I want failed, unknown, and missing tool checks handled consistently, so that LLM-drafted rows cannot overclaim.
- As a trainer, I want claim-level labels to mean the same thing across rows, so that the fine-tuned model learns reliable confidence calibration.

## 5. UX / UI requirements

No graphical UI.

The existing CLI commands must keep the same arguments and output style:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
python -m pytest tests/dataset
```

Validation errors should use the existing field-path style.

Example error:

```text
data/example.jsonl:3 row_id=bad_verified field=messages[2].content.claim_levels.correctness: verified requires a passing simulation or equivalence check
```

## 6. Functional requirements

### FR-1: Add explicit evidence-status helpers

Add helper functions in `scripts/dataset/validation.py` or a small helper module:

```python
def tool_check_status(row: dict, tool: str) -> str | None:
    ...

def has_tool_evidence(row: dict, tool: str) -> bool:
    ...

def has_passing_tool_evidence(row: dict, tool: str) -> bool:
    ...
```

Rules:

- Missing or null tool check means no evidence.
- Tool check must be an object with a valid `status` field to count.
- `has_passing_tool_evidence` returns true only for `status == "pass"`.
- `has_tool_evidence` returns true for `status in {"pass", "fail", "unknown"}` only when the row also has either a non-empty relevant artifact field or a non-empty tool-check `summary`.
- `status == "not_run"` never counts as evidence.

### FR-2: Strengthen `verified` claim-level validation

Update claim-level validation:

- `claim_levels.correctness == "verified"` requires `simulation` or `equivalence` with `status: "pass"`.
- `claim_levels.area == "verified"` requires `synthesis` with `status: "pass"`.
- `claim_levels.activity == "verified"` requires `toggle` with `status: "pass"`.
- `claim_levels.power == "verified"` requires `power` with `status: "pass"`.

If the relevant tool check is missing, null, `unknown`, `fail`, or `not_run`, validation must fail.

### FR-3: Strengthen `tool_supported` claim-level validation

Update claim-level validation:

- `tool_supported` requires relevant non-null tool evidence.
- `status == "not_run"` must fail.
- Missing summary and missing relevant artifact should fail.
- `status == "unknown"` may count only for report-explanation rows where the corresponding report artifact is non-empty.
- `status == "fail"` may count only when the answer is explaining a failed tool result or diagnostic, not when claiming an improvement.

For v0.1, implement the conservative minimum:

- Accept `tool_supported` when `has_tool_evidence(row, tool)` returns true.
- Keep existing unsupported-claim phrase checks for improvement claims.
- Add tests so `tool_supported` fails when the tool check is null, missing, or `not_run`.

### FR-4: Correct golden report claim levels

Review `data/golden/golden_v0.1.jsonl` and `scripts/dataset/build_seed_dataset.py`.

The golden synthesis report row currently describes state-bit/resource information and says area units are unavailable. Its `claim_levels.area` should not be `tool_supported` unless the answer makes a specific synthesis-report-supported area/resource claim and avoids measured area implication.

Safer acceptable options:

```json
"area": "insufficient_evidence"
```

or, if the wording is changed to explicitly mean resource-report support rather than area measurement:

```json
"area": "tool_supported"
```

Prefer `insufficient_evidence` for v0.1 to keep labels conservative.

Toggle report rows may keep:

```json
"activity": "tool_supported"
```

when a toggle report artifact is non-empty and the answer only explains activity evidence, not power.

### FR-5: Update schema documentation

Update `docs/dataset/claim_level_policy.md` to define evidence-status semantics:

- `verified`: requires pass status from an appropriate check.
- `tool_supported`: requires supplied tool/report evidence, not merely a placeholder object.
- `unknown`: can support report explanation but not verified claims.
- `fail`: can support diagnostic explanation but not improvement claims.
- `not_run`: never supports `tool_supported` or `verified`.

Update schema descriptions if useful, but do not introduce a new schema version.

### FR-6: Add tests

Add tests under `tests/dataset/` for:

- `verified` correctness fails with simulation status `unknown`.
- `verified` correctness fails with simulation status `fail`.
- `verified` correctness passes only with simulation or equivalence status `pass`.
- `tool_supported` fails when the relevant tool check is null.
- `tool_supported` fails when the relevant tool check has `status: "not_run"`.
- `tool_supported` passes for a report-explanation row with status `unknown`, non-empty summary, and matching non-empty report artifact.
- Golden dataset still validates with `--strict`.

### FR-7: Regenerate processed splits if golden rows change

If `data/golden/golden_v0.1.jsonl` changes, regenerate:

```text
data/processed/train.jsonl
data/processed/val.jsonl
data/processed/test.jsonl
data/processed/split_summary.json
```

Use:

```bash
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
```

## 7. Technical requirements

### 7.1 Architecture

Keep the current local dataset architecture. Do not add services, databases, or external tools.

Validation should remain layered:

```text
JSONL parse
-> row envelope
-> task schema
-> answer schema
-> golden quality
-> claim safety
-> claim-level evidence status
-> dataset-level checks
```

The exact order may differ if cleaner, but error reporting must remain clear.

### 7.2 Relevant tool mapping

Use this mapping:

```python
EVIDENCE_TOOLS = {
    "correctness": ("simulation", "equivalence"),
    "area": ("synthesis",),
    "activity": ("toggle",),
    "power": ("power",),
}
```

Use this artifact mapping for report-backed evidence:

```python
TOOL_ARTIFACTS = {
    "lint": "lint_log",
    "synthesis": "synthesis_report",
    "toggle": "toggle_report",
}
```

Correctness evidence may come from `simulation` or `equivalence` metadata. It does not need a `simulation_report` artifact in v0.1.

### 7.3 Security

- Do not execute tool logs or RTL artifacts.
- Do not add external downloads.
- Do not add private RTL.
- Do not add credentials or local environment paths.

### 7.4 Dependencies

Use Python standard library only.

## 8. Files likely involved

Likely modify:

```text
scripts/dataset/validation.py
scripts/dataset/build_seed_dataset.py
data/golden/golden_v0.1.jsonl
data/processed/train.jsonl
data/processed/val.jsonl
data/processed/test.jsonl
data/processed/split_summary.json
docs/dataset/claim_level_policy.md
tests/dataset/test_validation.py
tests/dataset/test_claim_safety.py
```

Do not modify unrelated files.

## 9. Data model

No new database and no new schema version.

The existing `tool_checks` object remains:

```json
{
  "simulation": {
    "status": "pass",
    "tool": "verilator",
    "version": null,
    "summary": "simulation passed",
    "artifact_ref": null
  }
}
```

The meaning of `status` becomes stricter:

- `pass`: supports `verified` for the matching domain.
- `fail`: supports diagnostic/tool explanation only.
- `unknown`: supports report explanation only when a report artifact or summary is present.
- `not_run`: never supports `tool_supported` or `verified`.

## 10. API contract

No HTTP APIs.

CLI contracts remain unchanged.

### Validate dataset

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Expected after implementation:

```text
Dataset validation passed.
Rows: 20
Errors: 0
Warnings: 0
```

### Split dataset

```bash
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
```

Expected after implementation:

- split succeeds,
- train/val/test files validate,
- no family overlap by default.

## 11. Edge cases

Handle:

- `tool_checks.simulation` missing.
- `tool_checks.simulation` null.
- `tool_checks.simulation.status == "not_run"`.
- `tool_checks.simulation.status == "unknown"`.
- `tool_checks.simulation.status == "fail"`.
- `tool_checks.simulation.status == "pass"`.
- `claim_levels.correctness == "verified"` with equivalence pass but simulation null.
- `claim_levels.area == "tool_supported"` with synthesis status unknown but no synthesis report artifact.
- `claim_levels.activity == "tool_supported"` with toggle status unknown and non-empty toggle report artifact.
- Report explanation rows where tool status is fail.
- Improvement wording with fail/unknown tool status.

## 12. Testing plan

Run:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
python -m pytest tests/dataset
```

Add targeted unit tests for evidence-status semantics.

## 13. Definition of done

The task is done only when:

- `verified` cannot pass with `unknown`, `fail`, `not_run`, null, or missing tool checks.
- `tool_supported` cannot pass with null or `not_run` tool checks.
- Report-explanation rows can still use supplied report artifacts conservatively.
- Golden synthesis-report row does not overstate area support.
- Golden dataset validates with `--strict`.
- Processed splits are regenerated if golden data changes.
- `python -m pytest tests/dataset` passes.
- Existing CLI behavior remains compatible.

## 14. Codex implementation instructions

Implement this focused evidence-status hardening spec.

Do not change the dataset format or schema version.

Do not add dependencies.

Do not implement training, public dataset conversion, external LLM calls, or EDA tool execution.

Update validation logic, claim-level policy documentation, golden dataset labels if needed, and tests.

Run the required validation, split, inspection, and pytest commands.

Commit and push when finished. Summarize changed files, commands run, test results, and tradeoffs.