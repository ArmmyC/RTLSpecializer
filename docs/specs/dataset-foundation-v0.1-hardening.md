# Feature Spec: Dataset Foundation v0.1 Hardening

## 1. Goal

Harden the existing dataset foundation so it is safe to use as the base for real RTL specialist training data.

The previous implementation created the expected repository structure, validation scripts, inspection scripts, split scripts, schema files, docs, tests, adapter skeletons, and a 20-row seed dataset. This hardening task fixes the quality and enforcement gaps before any training or public dataset conversion work begins.

The most important goal is to replace placeholder-style golden rows with real, minimal, self-contained RTL review examples where the issue, evidence, time reasoning, space reasoning, risks, and verification plan all match the supplied artifact.

This task should make `data/golden/golden_v0.1.jsonl` useful as a small trusted evaluation and smoke-training seed, not just a schema-valid fixture.

## 2. Non-goals

Do not build:

- QLoRA, DoRA, DPO, or any training scripts.
- Runtime model serving or inference APIs.
- Public dataset downloading.
- Public dataset conversion beyond current adapter skeletons.
- External LLM calls.
- Yosys, Verilator, simulation, synthesis, or toggle execution.
- A large dataset.
- Web UI or notebook UI.
- Private company RTL ingestion.

## 3. Assumptions

- The current dataset foundation implementation is kept and improved, not rewritten from scratch.
- The repo remains Python standard-library first.
- Python 3.10 or newer is acceptable.
- The current schema names remain `dataset_v0.1`, `rtl_task_v0.1`, and `rtl_answer_v0.1`.
- The v0.1 golden dataset should remain small, with at least 20 rows.
- All golden examples must be synthetic and public-safe.
- Tool evidence remains null unless explicitly represented as a synthetic report artifact and tool check metadata.
- The validator is the operational authority, but the JSON schema files should be aligned closely enough to be useful documentation.

## 4. User stories

- As a dataset builder, I want golden rows with real RTL snippets and matching evidence, so that the dataset teaches grounded RTL reasoning instead of generic pattern claims.
- As a reviewer, I want the validator to reject training-ready draft rows in processed splits, so that low-trust rows do not silently enter training files.
- As an evaluator, I want tests that catch placeholder artifacts, so that the seed dataset cannot regress into empty modules with invented issues.
- As a maintainer, I want schema files to document nested required fields, so that future contributors can create valid rows without reverse-engineering Python code.
- As a future trainer, I want train, validation, and test splits to contain only reviewed or validated rows, so that model training consumes trusted examples.

## 5. UX / UI requirements

This feature has no graphical UI.

The CLI behavior should remain compatible with the existing commands:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
python -m pytest tests/dataset
```

### 5.1 Validator UX additions

When a row has placeholder-like RTL artifacts, validation should fail or warn depending on severity.

Examples that should fail for golden reviewed rows:

```text
module sample_0; // synthetic illustrative RTL
endmodule
```

```text
module sample_1; // synthetic before RTL
endmodule
```

Examples that should pass:

```systemverilog
module counter_bug(
  input logic clk,
  input logic rst_n,
  input logic en,
  output logic [7:0] count
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) count <= 8'd0;
    else count <= count + 8'd1;
  end
endmodule
```

### 5.2 Error message format

Keep the current field-path error style. Add clear messages such as:

```text
data/golden/golden_v0.1.jsonl:7 row_id=golden_counter_activity_001 field=messages[1].content.artifacts.rtl_code: reviewed golden rows must contain substantive RTL, not placeholder modules
```

## 6. Functional requirements

### FR-1: Replace placeholder golden rows

Replace `data/golden/golden_v0.1.jsonl` with at least 20 reviewed synthetic rows whose artifacts contain substantive RTL, before/after RTL, or tool-report excerpts that match the row task.

A substantive RTL artifact must include at least one meaningful signal declaration, expression, assignment, always block, case statement, continuous assignment, or module interface relevant to the described issue.

The golden dataset must still satisfy the existing minimum task distribution:

```text
rtl_bug_review: 5
rtl_area_activity_review: 5
rtl_tool_report_explanation: 3
unsafe_optimization_rejection: 4
rtl_before_after_judgment: 3
```

The golden dataset must still cover at least these design families:

```text
counter
fsm
shift_register
mux
decoder
handshake
timer
register_bank
comparator
serializer
```

### FR-2: Make every golden issue grounded in artifact evidence

For each golden row:

- `issue_summary[].issue` must describe behavior visible in the supplied artifact.
- `issue_summary[].evidence.signal_names` must include at least one relevant signal name when RTL is supplied.
- `issue_summary[].evidence.code_location.module` must match the module name in the artifact when RTL is supplied.
- `issue_summary[].evidence.code_location.block` must identify a meaningful block such as `always_ff`, `always_comb`, `assign`, `case`, `lint_log`, `synthesis_report`, or `toggle_report`.
- `issue_summary[].evidence.reason` must explain the concrete RTL or report evidence.

Do not use generic evidence such as:

```text
The supplied synthetic artifact motivates review but does not provide tool proof.
```

### FR-3: Improve seed dataset IDs

Update row IDs to be stable and descriptive.

Preferred format:

```text
golden_<design_family>_<task_short>_<number>
```

Examples:

```text
golden_counter_activity_001
golden_fsm_bug_002
golden_handshake_reject_003
```

Avoid IDs that are only the family plus sequence number, such as `golden_counter_001`.

### FR-4: Add substantive-artifact validation for reviewed golden rows

Add a validator rule that applies when all conditions are true:

- `source == "handwritten_golden"`
- `review_status == "reviewed"`
- artifact field is expected to contain RTL for that task

The validator must reject placeholder-only RTL.

Reject RTL artifacts when any of these are true:

- artifact contains only an empty module and comments,
- artifact contains `// synthetic illustrative RTL`,
- artifact contains `// synthetic before RTL`,
- artifact contains `// synthetic proposed RTL`,
- artifact has no meaningful assignment, always block, case statement, or signal declaration beyond a bare module declaration.

The rule should not reject tool-report-only rows when the relevant report artifact is populated.

### FR-5: Enforce training-ready review status for train, val, and test rows

Update `validate_dataset_file` so that rows with `split` equal to `train`, `val`, or `test` must have:

```text
review_status == validated or reviewed
```

Rows with `split == unsplit` may still be `draft`, `validated`, `reviewed`, or `rejected`, although `rejected` should trigger a warning.

This prevents manually edited processed files from passing validation when they contain draft rows.

### FR-6: Strengthen schema files

Update the JSON schema files so they better match the operational validator.

At minimum:

- `schemas/rtl_task_v0.1.schema.json` must define nested required fields for `design_context`, `artifacts`, `extracted_rtl_summary`, and `constraints`.
- `schemas/rtl_answer_v0.1.schema.json` must define nested required fields for `issue_summary[].evidence`, `time_reasoning`, `space_reasoning`, `safe_optimization`, `claim_levels`, and `patch`.
- `schemas/dataset_row_v0.1.schema.json` must define nested required fields for `provenance`, `tool_checks`, and `messages` role order as much as JSON Schema can reasonably express.
- The schema files must not contradict `scripts/dataset/constants.py` or `scripts/dataset/validation.py`.

Do not add `jsonschema` as a runtime dependency unless the repo already uses it or there is a strong reason.

### FR-7: Expand validator tests

Add or update tests to cover:

- reviewed golden rows with placeholder-only RTL fail validation,
- reviewed golden rows with substantive RTL pass validation,
- train, val, or test rows with `review_status: draft` fail validation,
- unsplit draft rows may pass schema validation but should not be split without `--allow-unreviewed`,
- row IDs follow the descriptive pattern for golden rows,
- evidence signal names are non-empty for RTL-backed golden rows,
- evidence module names match supplied RTL module names for RTL-backed golden rows.

### FR-8: Preserve existing CLI contracts

Do not break existing CLI arguments or output formats.

The following commands must still work:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
```

### FR-9: Refresh generated processed splits

After replacing the golden dataset, regenerate:

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

The generated split files must validate successfully.

## 7. Technical requirements

### 7.1 Architecture

Keep the current architecture:

```text
data/golden/golden_v0.1.jsonl
  -> scripts/dataset/validate_dataset.py
  -> scripts/dataset/inspect_dataset.py
  -> scripts/dataset/split_dataset.py
  -> data/processed/*.jsonl
```

Add validation helpers inside the existing `scripts/dataset/validation.py` or a small dedicated helper module. Do not rewrite the entire validator.

Suggested helper functions:

```python
def is_placeholder_rtl(text: str) -> bool:
    ...

def extract_module_names(text: str) -> list[str]:
    ...

def artifact_has_substantive_rtl(text: str) -> bool:
    ...
```

These functions can use conservative regular expressions. They do not need to be full SystemVerilog parsers.

### 7.2 Substantive RTL heuristic

A SystemVerilog/Verilog artifact should count as substantive if it contains a module declaration plus at least one of:

- `input`, `output`, or `logic` declarations with signal names,
- `assign` statement,
- `always_ff`, `always_comb`, or `always @`,
- nontrivial procedural assignment using `<=` or `=`,
- `case` statement,
- `if` statement inside an always block,
- array or memory declaration.

The heuristic should reject a bare module with only comments.

### 7.3 Golden row quality rules

For `handwritten_golden` and `review_status: reviewed` rows:

- RTL-backed rows require substantive RTL.
- Tool-report explanation rows may be report-backed instead of RTL-backed.
- Before/after judgment rows require both `before_rtl_code` and `after_rtl_code` to be substantive.
- Area/activity review rows must not claim measured improvement without tool metadata.
- Every row must use concrete signal names in evidence when RTL is supplied.

### 7.4 Security

- Continue treating dataset files as untrusted input.
- Do not execute RTL, shell commands, Python snippets, or tool-log content embedded in rows.
- Do not add public dataset downloads.
- Do not add private RTL.
- Do not add local absolute paths, API keys, environment files, model paths, or credentials.

### 7.5 Dependencies

Prefer standard library only. Do not add new dependencies unless necessary.

## 8. Files likely involved

Likely modify:

```text
data/golden/golden_v0.1.jsonl
data/processed/train.jsonl
data/processed/val.jsonl
data/processed/test.jsonl
data/processed/split_summary.json
schemas/rtl_task_v0.1.schema.json
schemas/rtl_answer_v0.1.schema.json
schemas/dataset_row_v0.1.schema.json
scripts/dataset/build_seed_dataset.py
scripts/dataset/validation.py
tests/dataset/test_validation.py
tests/dataset/test_split_dataset.py
```

Optionally modify:

```text
docs/dataset/dataset_guidelines.md
docs/dataset/claim_level_policy.md
README.md
```

Do not modify unrelated files.

## 9. Data model

No database changes.

The data model remains JSONL rows using the existing envelope:

```text
dataset row metadata
+ provenance
+ tool_checks
+ messages[system, user rtl_task_v0.1, assistant rtl_answer_v0.1]
```

The golden dataset content quality changes, but the row format should remain backward-compatible with the v0.1 validator.

## 10. API contract

There are no HTTP APIs.

The CLI contracts remain unchanged.

### 10.1 Validate dataset

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Expected result after this task:

```json
{
  "ok": true,
  "rows": 20,
  "errors": [],
  "warnings": []
}
```

The text output may continue to use the current format.

### 10.2 Inspect dataset

```bash
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
```

Expected result:

- `rows == 20` or more,
- all required task types represented,
- at least 10 design families represented,
- no duplicate IDs.

### 10.3 Split dataset

```bash
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
```

Expected result:

- train, val, test JSONL files written,
- `split_summary.json` written,
- processed split files validate,
- no design family overlap by default.

## 11. Edge cases

Handle these edge cases:

- RTL contains comments plus real logic.
- RTL contains `assign` but no always block.
- RTL contains `always @*` instead of `always_comb`.
- RTL contains `reg` and `wire` instead of SystemVerilog `logic`.
- Before/after rows have one substantive side and one placeholder side.
- Tool-report rows have no RTL but have a relevant report artifact.
- Golden row evidence has empty `signal_names` despite RTL artifact.
- Golden row evidence module name does not appear in the RTL artifact.
- Processed train row has `review_status: draft`.
- Unsplit draft row is used for validation but not splitting.
- Placeholder marker appears inside a comment in otherwise substantive RTL.
- Existing tests assume exactly 20 seed rows.

## 12. Testing plan

### 12.1 Unit tests

Add tests for:

- `artifact_has_substantive_rtl` accepts a small always_ff counter.
- `artifact_has_substantive_rtl` accepts a continuous assign mux.
- `artifact_has_substantive_rtl` rejects an empty module with only comments.
- `extract_module_names` returns module names from simple RTL.
- golden reviewed rows reject placeholder RTL.
- train/val/test draft rows fail validation.
- unsplit draft rows pass validation but split command rejects them without `--allow-unreviewed`.

### 12.2 Integration tests

Run and assert success:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir <tmpdir> --seed 7
python -m pytest tests/dataset
```

### 12.3 UI tests

Not applicable.

### 12.4 Manual checks

Manually inspect at least five golden rows and confirm:

- the RTL artifact contains the named issue,
- signal names in evidence appear in the artifact,
- time reasoning references actual clock/reset/state behavior when relevant,
- space reasoning references actual resources,
- claim levels are conservative.

## 13. Definition of done

The task is complete only when:

- Golden seed rows no longer use placeholder-only RTL.
- Every golden reviewed RTL-backed row has substantive RTL.
- Every golden reviewed RTL-backed row has concrete evidence linked to supplied artifacts.
- Validator catches placeholder-only RTL for reviewed golden rows.
- Validator rejects train/val/test rows with draft or rejected review status.
- Schema files are strengthened and aligned with validator behavior.
- Processed split files are regenerated from the hardened golden dataset.
- `python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict` passes.
- `python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7` passes.
- `python -m pytest tests/dataset` passes.
- No private RTL, proprietary logs, local paths, tokens, or credentials are committed.
- Existing CLI contracts are preserved.

## 14. Codex implementation instructions

Implement this hardening spec.

Focus only on improving the current dataset foundation. Do not start training or public dataset conversion.

Do not rewrite the project from scratch.

Do not introduce new dependencies unless absolutely necessary.

Use standard-library Python helpers for placeholder RTL detection and simple module-name extraction.

Replace the current placeholder golden examples with at least 20 small, concrete, synthetic, public-safe RTL/report examples. Keep them concise but real enough that the issue and evidence are visible in the artifact.

Update the validator, schemas, tests, and generated processed splits accordingly.

Run:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
python -m pytest tests/dataset
```

If there are existing repo-level lint or typecheck commands, run those too.

After finishing, commit and push the changes. Summarize changed files, commands run, test results, and tradeoffs.