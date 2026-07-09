# Feature Spec: Dataset Foundation v0.1

## 1. Goal

Build the dataset-first foundation for `RTLSpecializer`.

The feature creates the first production-quality dataset pipeline for training and evaluating an RTL specialist model. The immediate goal is not fine-tuning. The immediate goal is to make it possible to create, validate, inspect, split, and maintain high-quality structured supervised fine-tuning rows using the project schemas:

```text
rtl_task_v0.1 -> rtl_answer_v0.1
```

This feature should let the repo support three dataset sources:

1. Hand-written golden examples.
2. Public RTL benchmark examples converted into the project format.
3. LLM-drafted examples that are accepted only after schema and claim-safety validation.

The dataset foundation must enforce conservative RTL engineering behavior:

- Do not claim measured power improvement without a real power report.
- Do not claim area improvement without synthesis evidence.
- Do not claim correctness is verified without simulation, equivalence, or explicit tool evidence.
- Do not suggest timing, reset, latency, or interface changes without risk warnings.
- Prefer structured, evidence-based answers over vague natural language.

The final result should be a repo state where Codex or a developer can run one command to validate dataset rows and get a clear pass/fail report.

## 2. Non-goals

This task must not build:

- Model fine-tuning scripts for QLoRA, DoRA, DPO, or full fine-tuning.
- Runtime inference APIs.
- Web UI, dashboard UI, or notebook UI.
- Automatic downloading from public benchmark repositories.
- Direct calls to paid or external LLM APIs.
- Full RTL generation training.
- Company/private RTL ingestion.
- Power estimation flows.
- Yosys, Verilator, or simulator integration beyond placeholder metadata fields.
- Automatic human review workflow.
- A large 1,000+ example dataset.

## 3. Assumptions

- The repo is currently early-stage and can accept a clean dataset-first structure.
- The default branch is `main`.
- The project is Python-based or can include Python scripts for dataset tooling.
- Python 3.10 or newer is acceptable.
- External dependencies should be avoided for the first implementation unless already present.
- The first dataset version is `dataset_v0.1`.
- The first task schema is `rtl_task_v0.1`.
- The first answer schema is `rtl_answer_v0.1`.
- Dataset rows will be stored as JSONL.
- Each JSONL line will include provenance metadata plus chat-style SFT messages.
- LLM conversion from public datasets will be handled as an offline draft step for now. The repo should validate LLM-produced drafts, not call the LLM itself.
- Public dataset conversion support should start with adapter skeletons and a stable input/output contract.
- The first implementation should include a small seed golden dataset. It should be enough to test tooling, not enough for real training.

## 4. User stories

- As a dataset builder, I want strict JSONL validation, so that bad training rows are rejected before they enter the dataset.
- As an RTL researcher, I want fixed task and answer schemas, so that the fine-tuned model learns a stable structured mapping.
- As an evaluator, I want dataset statistics by task type, split, source, claim level, and design family, so that I can see whether the dataset is balanced.
- As a reviewer, I want every row to include provenance and tool-check metadata, so that I can trace where the row came from and what evidence supports it.
- As a model trainer, I want train, validation, and test JSONL outputs, so that training scripts can consume stable files later.
- As a project maintainer, I want claim-safety checks, so that examples with unsupported power, area, or correctness claims are flagged automatically.
- As a future public-dataset converter, I want a documented draft format, so that LLM-converted examples can be reviewed and validated consistently.

## 5. UX / UI requirements

This feature has no graphical UI. The UX is command-line based.

### 5.1 CLI commands

Implement commands under `scripts/dataset/` that can be run from repo root.

Required commands:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
```

Optional command if time allows:

```bash
python scripts/dataset/convert_llm_draft.py --input data/drafts/example_llm_draft.jsonl --output data/processed/converted_v0.1.jsonl
```

### 5.2 CLI output states

#### Success state

The validator should print:

```text
Dataset validation passed.

Rows: 50
Errors: 0
Warnings: 0

Task types:
- rtl_bug_review: 15
- rtl_area_activity_review: 15
- rtl_tool_report_explanation: 5
- unsafe_optimization_rejection: 10
- rtl_before_after_judgment: 5
```

Exit code: `0`.

#### Warning state

Warnings should not fail the process unless `--strict` is used.

Example:

```text
Dataset validation passed with warnings.

Rows: 50
Errors: 0
Warnings: 2

Warnings:
- line 18: row has no synthesis report, area claim_level must remain suggestion_only or insufficient_evidence
- line 27: evidence.code_location.line_range is missing
```

Exit code:

- `0` by default.
- `1` when `--strict` is set.

#### Error state

Errors must fail.

Example:

```text
Dataset validation failed.

Rows: 50
Errors: 3
Warnings: 1

Errors:
- line 4: missing messages[2].content.verification_plan
- line 9: invalid claim_level "proved"
- line 21: unsupported phrase "power is reduced" without power_report evidence
```

Exit code: `1`.

#### Empty state

If input file exists but has no rows:

```text
Dataset validation failed.

Rows: 0
Errors: 1

Errors:
- file is empty
```

Exit code: `1`.

#### Missing file state

If input file does not exist:

```text
Dataset validation failed.

Errors:
- input file not found: data/golden/golden_v0.1.jsonl
```

Exit code: `1`.

### 5.3 Responsive behavior

Not applicable. There is no UI.

### 5.4 Error formatting

Errors must include:

- file path,
- line number if applicable,
- row id if available,
- field path if applicable,
- human-readable message.

Example:

```text
data/golden/golden_v0.1.jsonl:12 row_id=golden_counter_enable_003 field=messages[2].content.claim_levels.power: power claim requires power_report evidence
```

## 6. Functional requirements

### FR-1: Create dataset folder structure

Create the following folders:

```text
data/
  README.md
  golden/
  raw_public/
  drafts/
  processed/
  heldout/
schemas/
docs/
  dataset/
  specs/
scripts/
  dataset/
tests/
  dataset/
```

Each data subfolder should include a `.gitkeep` if it would otherwise be empty.

### FR-2: Add schema files

Create these schema files:

```text
schemas/rtl_task_v0.1.schema.json
schemas/rtl_answer_v0.1.schema.json
schemas/dataset_row_v0.1.schema.json
```

The schemas must document required fields, enum values, field descriptions, and examples.

The implementation may validate via Python code instead of a JSON Schema library, but the schema files must still be present and aligned with the validator.

### FR-3: Define allowed task types

The validator must allow exactly these task types for v0.1:

```text
rtl_bug_review
rtl_area_activity_review
rtl_tool_report_explanation
unsafe_optimization_rejection
rtl_before_after_judgment
```

Any other task type must be rejected.

### FR-4: Define allowed user goals

The validator must allow exactly these user goals for v0.1:

```text
find_correctness_bug
reduce_switching_activity
reduce_area
explain_lint_log
explain_synthesis_report
explain_toggle_report
compare_before_after
suggest_safe_patch
reject_unsafe_optimization
```

Any other user goal must be rejected.

### FR-5: Define allowed claim levels

The validator must support per-domain claim levels.

Required answer field:

```json
"claim_levels": {
  "correctness": "suggestion_only",
  "area": "insufficient_evidence",
  "activity": "suggestion_only",
  "power": "insufficient_evidence"
}
```

Allowed values:

```text
suggestion_only
tool_supported
verified
insufficient_evidence
not_applicable
```

The older single field `claim_level` must not be used in v0.1 dataset rows except as a migration-only warning if present. If `claim_level` appears, validator should warn and require `claim_levels` to also exist.

### FR-6: Define dataset row envelope

Each JSONL line must be a JSON object with this top-level structure:

```json
{
  "id": "golden_counter_enable_001",
  "dataset_version": "dataset_v0.1",
  "split": "train",
  "source": "handwritten_golden",
  "license": "project_internal",
  "design_family": "counter",
  "task_family": "rtl_area_activity_review",
  "created_by": "human",
  "review_status": "reviewed",
  "provenance": {
    "origin": "handwritten",
    "public_dataset_name": null,
    "public_dataset_url": null,
    "source_commit": null,
    "notes": "Synthetic counter example."
  },
  "tool_checks": {
    "parse": null,
    "lint": null,
    "simulation": null,
    "equivalence": null,
    "synthesis": null,
    "toggle": null,
    "power": null
  },
  "messages": []
}
```

Required top-level fields:

- `id`
- `dataset_version`
- `split`
- `source`
- `license`
- `design_family`
- `task_family`
- `created_by`
- `review_status`
- `provenance`
- `tool_checks`
- `messages`

### FR-7: Validate split values

Allowed split values:

```text
train
val
test
unsplit
```

Rows in `data/golden/golden_v0.1.jsonl` may use `unsplit`.

Rows written to `data/processed/train.jsonl`, `data/processed/val.jsonl`, and `data/processed/test.jsonl` must use their matching split values.

### FR-8: Validate source values

Allowed source values:

```text
handwritten_golden
synthetic_rfid_style
public_verilog_eval
public_rtllm
public_rtllm_2
public_rtlfixer
public_openllm_rtl
teacher_generated
llm_converted_public
```

### FR-9: Validate review status values

Allowed review status values:

```text
draft
validated
reviewed
rejected
```

Training-ready rows must have `review_status` equal to either `validated` or `reviewed`.

### FR-10: Validate messages format

Each row must contain exactly three messages:

1. system message,
2. user message with `rtl_task_v0.1`,
3. assistant message with `rtl_answer_v0.1`.

Required shape:

```json
"messages": [
  {
    "role": "system",
    "content": "..."
  },
  {
    "role": "user",
    "content": {
      "schema_version": "rtl_task_v0.1"
    }
  },
  {
    "role": "assistant",
    "content": {
      "schema_version": "rtl_answer_v0.1"
    }
  }
]
```

The validator must reject rows with missing roles, wrong role order, missing content, or extra messages.

### FR-11: Validate `rtl_task_v0.1`

The user message content must include:

```json
{
  "schema_version": "rtl_task_v0.1",
  "domain": "digital_rtl",
  "task_type": "rtl_area_activity_review",
  "user_goal": "reduce_switching_activity",
  "design_context": {
    "target_domain": "rfid_nfc_digital_ic",
    "priority": ["correctness", "low_switching_activity", "low_area"],
    "timing_policy": "timing_is_constraint_not_reward"
  },
  "artifacts": {
    "rtl_code": null,
    "before_rtl_code": null,
    "after_rtl_code": null,
    "testbench": null,
    "synthesis_report": null,
    "toggle_report": null,
    "lint_log": null
  },
  "extracted_rtl_summary": {
    "top_module": null,
    "clock_signals": [],
    "reset_signals": [],
    "registered_signals": [],
    "combinational_blocks": [],
    "suspected_fsm_signals": [],
    "suspected_counters": [],
    "unused_enable_signals": [],
    "activity_hotspots": []
  },
  "constraints": {
    "preserve_top_level_interface": true,
    "preserve_cycle_level_behavior": true,
    "preserve_reset_behavior": true,
    "do_not_claim_power_without_power_report": true,
    "prefer_minimal_patch": true
  },
  "assumptions": [],
  "required_output": [
    "issue_summary",
    "time_reasoning",
    "space_reasoning",
    "safe_optimization",
    "functional_risk",
    "verification_plan",
    "claim_levels"
  ]
}
```

Validation rules:

- `schema_version` must be `rtl_task_v0.1`.
- `domain` must be `digital_rtl`.
- `task_type` must match the row `task_family`.
- At least one artifact field must be non-null and non-empty.
- `constraints.do_not_claim_power_without_power_report` must be `true`.
- `constraints.preserve_cycle_level_behavior` must be `true` unless the task is explicitly `unsafe_optimization_rejection`.
- `required_output` must include all required answer sections.

### FR-12: Validate `rtl_answer_v0.1`

The assistant message content must include:

```json
{
  "schema_version": "rtl_answer_v0.1",
  "task_type": "rtl_area_activity_review",
  "issue_summary": [
    {
      "issue": "...",
      "severity": "low",
      "evidence": {
        "signal_names": [],
        "code_location": {
          "module": null,
          "block": null,
          "line_range": null
        },
        "reason": "..."
      }
    }
  ],
  "time_reasoning": {
    "clock_cycle_behavior": "...",
    "latency_or_state_risk": "...",
    "reset_behavior_risk": "..."
  },
  "space_reasoning": {
    "hardware_resources_involved": [],
    "area_risk": "...",
    "activity_risk": "..."
  },
  "safe_optimization": {
    "recommendation": "...",
    "patch_style": "minimal",
    "expected_effect": "...",
    "requires_spec_confirmation": false
  },
  "functional_risk": [],
  "verification_plan": [],
  "claim_levels": {
    "correctness": "suggestion_only",
    "area": "insufficient_evidence",
    "activity": "suggestion_only",
    "power": "insufficient_evidence"
  },
  "patch": {
    "provided": false,
    "patch_type": "none",
    "diff": null,
    "notes": null
  }
}
```

Validation rules:

- `schema_version` must be `rtl_answer_v0.1`.
- `task_type` must match the user task type.
- `issue_summary` must be a non-empty list.
- Each issue severity must be one of `low`, `medium`, `high`.
- `functional_risk` must be non-empty when `safe_optimization.patch_style` is not `explanation_only`.
- `verification_plan` must include at least `lint/compile`.
- Area/activity review tasks must include `synthesis area comparison` or a clear note that synthesis evidence is unavailable.
- Activity review tasks must include `VCD toggle/activity comparison` or a clear note that toggle evidence is unavailable.

### FR-13: Implement claim-safety checks

The validator must scan answer text fields for unsupported claims.

It must reject rows when:

- The answer claims power improvement and `tool_checks.power` is null or absent.
- The answer claims measured power improvement but artifacts do not include a power report.
- The answer claims area improved and `tool_checks.synthesis` is null or absent.
- The answer claims switching/toggle improvement and `tool_checks.toggle` is null or absent.
- The answer claims correctness is verified and both `tool_checks.simulation` and `tool_checks.equivalence` are null or absent.
- The answer says a patch is safe without a functional risk warning.
- The answer suggests removing or changing reset behavior without a reset behavior risk warning.
- The answer suggests removing registers, sharing hardware, or changing valid/ready behavior without a latency/state/interface risk warning.

The validator should allow weaker language such as:

```text
may reduce switching activity
could reduce area after synthesis confirmation
requires simulation or equivalence to verify correctness
```

The validator should reject stronger unsupported language such as:

```text
reduces power
area is improved
correctness is verified
this patch is safe
guaranteed lower activity
```

### FR-14: Add seed golden dataset

Create:

```text
data/golden/golden_v0.1.jsonl
```

The file must contain at least 20 valid rows. Prefer 50 rows if practical in the implementation pass.

Minimum required distribution for the first implementation:

```text
rtl_bug_review: 5
rtl_area_activity_review: 5
rtl_tool_report_explanation: 3
unsafe_optimization_rejection: 4
rtl_before_after_judgment: 3
```

Target distribution if Codex can reasonably create 50 rows:

```text
rtl_bug_review: 15
rtl_area_activity_review: 15
rtl_tool_report_explanation: 5
unsafe_optimization_rejection: 10
rtl_before_after_judgment: 5
```

Seed rows must cover at least these design families:

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

### FR-15: Add dataset inspection command

Create:

```text
scripts/dataset/inspect_dataset.py
```

It must print:

- total rows,
- row count by split,
- row count by source,
- row count by task type,
- row count by design family,
- row count by review status,
- claim-level distribution by domain,
- rows missing tool evidence,
- duplicate IDs if any.

It must support JSON output:

```bash
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
```

### FR-16: Add dataset split command

Create:

```text
scripts/dataset/split_dataset.py
```

Input:

```bash
python scripts/dataset/split_dataset.py \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/processed \
  --train-ratio 0.70 \
  --val-ratio 0.15 \
  --test-ratio 0.15 \
  --seed 7
```

Output:

```text
data/processed/train.jsonl
data/processed/val.jsonl
data/processed/test.jsonl
data/processed/split_summary.json
```

Rules:

- Preserve rows exactly except for updating `split`.
- Prefer splitting by `design_family`, not random row-level splitting.
- No `design_family` may appear in more than one split unless `--allow-family-overlap` is explicitly passed.
- Reject invalid ratios unless they sum to 1.0 within a small tolerance.
- Validate all output files after writing.

### FR-17: Add LLM draft conversion contract

Create documentation:

```text
docs/dataset/llm_conversion_contract.md
```

This document must specify how an offline LLM should produce draft rows.

The contract must say:

- The LLM output is draft only.
- The LLM must not invent tool evidence.
- The LLM must use `insufficient_evidence` or `suggestion_only` when no tool evidence exists.
- The validator is the authority.
- Human review is required before public converted rows become training-ready.

### FR-18: Add public dataset adapter skeleton

Create:

```text
scripts/dataset/adapters/
  __init__.py
  base.py
  verilog_eval.py
  rtllm.py
  rtlfixer.py
```

For v0.1, these may be skeletons with clear TODOs and stable interfaces.

Required base interface:

```python
class PublicDatasetAdapter:
    name: str

    def discover_examples(self, root: Path) -> list[RawPublicExample]:
        ...

    def to_draft_row(self, example: RawPublicExample) -> dict:
        ...
```

The skeleton must not download data from the internet.

### FR-19: Add tests

Create tests under:

```text
tests/dataset/
```

Required tests:

- valid seed dataset passes validation,
- missing required top-level field fails,
- invalid task type fails,
- invalid claim level fails,
- unsupported power claim fails,
- unsupported verified correctness claim fails,
- split command creates train/val/test files,
- split command prevents design family overlap by default,
- inspect command returns expected counts,
- duplicate row IDs are flagged.

### FR-20: Add README documentation

Create or update:

```text
data/README.md
docs/dataset/dataset_guidelines.md
docs/dataset/claim_level_policy.md
docs/dataset/public_dataset_sources.md
```

The docs must explain:

- dataset row structure,
- how to add a new row,
- how to validate rows,
- how to split rows,
- what each claim level means,
- how public datasets can be converted safely,
- why random public Verilog should not be used without validation,
- why company/private RTL must not be committed.

## 7. Technical requirements

### 7.1 Architecture

Implement the feature as a local dataset tooling layer.

```text
Raw examples
  -> optional adapter skeleton
  -> optional LLM draft outside repo
  -> dataset row JSONL
  -> validator
  -> inspector
  -> splitter
  -> processed train/val/test JSONL
```

Core modules should be reusable from CLI scripts.

Suggested internal module layout:

```text
scripts/dataset/
  __init__.py
  constants.py
  io_utils.py
  validation.py
  claim_safety.py
  inspect_dataset.py
  validate_dataset.py
  split_dataset.py
  convert_llm_draft.py
  adapters/
    __init__.py
    base.py
    verilog_eval.py
    rtllm.py
    rtlfixer.py
```

### 7.2 Data flow

#### Golden dataset flow

```text
data/golden/golden_v0.1.jsonl
  -> validate_dataset.py
  -> inspect_dataset.py
  -> split_dataset.py
  -> data/processed/train.jsonl
  -> data/processed/val.jsonl
  -> data/processed/test.jsonl
```

#### Public dataset draft flow

```text
data/raw_public/<dataset-name>/
  -> adapter skeleton discovers raw examples
  -> offline LLM or manual conversion creates draft rows
  -> data/drafts/<dataset-name>_draft.jsonl
  -> validate_dataset.py
  -> manual review
  -> data/processed/public_converted_v0.1.jsonl
```

### 7.3 Validation rules

Validation must happen in layers:

1. JSONL parsing.
2. Top-level row envelope validation.
3. Message structure validation.
4. Task schema validation.
5. Answer schema validation.
6. Cross-field consistency validation.
7. Claim-safety validation.
8. Dataset-level validation.

Dataset-level validation includes:

- duplicate IDs,
- distribution warnings,
- split consistency,
- design family overlap,
- empty files.

### 7.4 Permissions and security

- Do not commit private RTL.
- Do not commit company tool logs.
- Do not commit PDK, fab, or proprietary implementation details.
- Do not add API keys, tokens, local model paths, or `.env` files.
- Do not download public datasets automatically.
- Public dataset rows must include source and license metadata.
- Rows with uncertain license must use `review_status: "draft"` and must not be included in train/val/test output unless explicitly allowed by a CLI flag.
- The validator must not execute RTL, scripts, or shell commands embedded in dataset files.
- Treat dataset files as untrusted input.

### 7.5 Dependencies

Preferred: standard library only.

Allowed standard modules:

```text
argparse
json
pathlib
dataclasses
typing
collections
random
re
sys
textwrap
```

If the repo already uses `pytest`, use it for tests.

Do not introduce `jsonschema` unless there is already a dependency file and adding it is clearly better than manual validation. If added, update dependency files and document why.

### 7.6 Compatibility

- The scripts must run from repo root.
- Paths must work on Linux, macOS, and Windows.
- Use UTF-8 explicitly when reading/writing files.
- Newline-delimited JSON must use one object per line.
- Scripts must not require GPU.
- Scripts must not require EDA tools.

## 8. Files likely involved

Create or modify:

```text
docs/specs/dataset-foundation-v0.1.md
data/README.md
data/golden/golden_v0.1.jsonl
data/raw_public/.gitkeep
data/drafts/.gitkeep
data/processed/.gitkeep
data/heldout/.gitkeep
schemas/rtl_task_v0.1.schema.json
schemas/rtl_answer_v0.1.schema.json
schemas/dataset_row_v0.1.schema.json
scripts/dataset/__init__.py
scripts/dataset/constants.py
scripts/dataset/io_utils.py
scripts/dataset/validation.py
scripts/dataset/claim_safety.py
scripts/dataset/validate_dataset.py
scripts/dataset/inspect_dataset.py
scripts/dataset/split_dataset.py
scripts/dataset/convert_llm_draft.py
scripts/dataset/adapters/__init__.py
scripts/dataset/adapters/base.py
scripts/dataset/adapters/verilog_eval.py
scripts/dataset/adapters/rtllm.py
scripts/dataset/adapters/rtlfixer.py
tests/dataset/test_validation.py
tests/dataset/test_claim_safety.py
tests/dataset/test_split_dataset.py
tests/dataset/test_inspect_dataset.py
docs/dataset/dataset_guidelines.md
docs/dataset/claim_level_policy.md
docs/dataset/llm_conversion_contract.md
docs/dataset/public_dataset_sources.md
```

If the repo has no test framework yet, add the minimal config needed to run pytest only if appropriate:

```text
pyproject.toml
```

Do not add this if an existing test/config pattern is present.

## 9. Data model

No database is required.

The data model is file-based JSONL.

### 9.1 Dataset row

Type: JSON object, one per JSONL line.

Fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | string | yes | Unique stable row ID. |
| `dataset_version` | string | yes | Must be `dataset_v0.1`. |
| `split` | string | yes | `train`, `val`, `test`, or `unsplit`. |
| `source` | string | yes | Controlled source enum. |
| `license` | string | yes | License or usage label. |
| `design_family` | string | yes | General design family used for split isolation. |
| `task_family` | string | yes | Same value as task `task_type`. |
| `created_by` | string | yes | `human`, `teacher_model`, `script`, or similar. |
| `review_status` | string | yes | `draft`, `validated`, `reviewed`, or `rejected`. |
| `provenance` | object | yes | Origin metadata. |
| `tool_checks` | object | yes | Evidence metadata. |
| `messages` | array | yes | Three-message SFT row. |

Recommended ID format:

```text
<source>_<design_family>_<task_type_short>_<number>
```

Example:

```text
golden_counter_activity_001
```

### 9.2 Provenance object

Fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| `origin` | string | yes | Human-readable origin. |
| `public_dataset_name` | string or null | yes | Public dataset name when applicable. |
| `public_dataset_url` | string or null | yes | Public dataset URL when applicable. |
| `source_commit` | string or null | yes | Source commit when available. |
| `notes` | string | yes | Brief notes. |

### 9.3 Tool checks object

Fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| `parse` | object or null | yes | Parser result metadata. |
| `lint` | object or null | yes | Lint result metadata. |
| `simulation` | object or null | yes | Simulation result metadata. |
| `equivalence` | object or null | yes | Equivalence result metadata. |
| `synthesis` | object or null | yes | Synthesis result metadata. |
| `toggle` | object or null | yes | VCD or toggle result metadata. |
| `power` | object or null | yes | Real power report metadata. Usually null in v0.1. |

Suggested tool check shape when present:

```json
{
  "status": "pass",
  "tool": "verilator",
  "version": null,
  "summary": "lint passed",
  "artifact_ref": null
}
```

Allowed status values:

```text
pass
fail
not_run
unknown
```

### 9.4 `rtl_task_v0.1`

See FR-11.

### 9.5 `rtl_answer_v0.1`

See FR-12.

### 9.6 Indexes

No database indexes required.

The validator should internally build:

- set of row IDs for duplicate detection,
- map of design family to split,
- counters by task type, source, design family, and review status.

### 9.7 Migrations

No database migration required.

Add a migration note in `docs/dataset/dataset_guidelines.md`:

```text
When moving from dataset_v0.1 to dataset_v0.2, create a new migration script instead of editing old rows in place.
```

## 10. API contract

There are no HTTP APIs in this feature. The API surface is CLI commands plus reusable Python functions.

### 10.1 Validate dataset

- Name: Validate Dataset
- Method: CLI
- Path: `scripts/dataset/validate_dataset.py`

Request:

```bash
python scripts/dataset/validate_dataset.py \
  --input data/golden/golden_v0.1.jsonl \
  [--strict] \
  [--json]
```

Response body, text mode:

```text
Dataset validation passed.

Rows: 20
Errors: 0
Warnings: 0
```

Response body, JSON mode:

```json
{
  "ok": true,
  "rows": 20,
  "errors": [],
  "warnings": [],
  "summary": {
    "by_task_type": {
      "rtl_bug_review": 5
    }
  }
}
```

Error cases:

- input file missing,
- file is empty,
- malformed JSON on a line,
- missing required field,
- invalid enum value,
- invalid messages shape,
- unsupported claim,
- duplicate row ID,
- split/design family conflict.

Exit codes:

- `0` when valid,
- `1` when errors exist,
- `1` when warnings exist and `--strict` is used.

### 10.2 Inspect dataset

- Name: Inspect Dataset
- Method: CLI
- Path: `scripts/dataset/inspect_dataset.py`

Request:

```bash
python scripts/dataset/inspect_dataset.py \
  --input data/golden/golden_v0.1.jsonl \
  [--json]
```

Response body, text mode:

```text
Dataset inspection

Rows: 20

By task type:
- rtl_bug_review: 5
- rtl_area_activity_review: 5
```

Response body, JSON mode:

```json
{
  "rows": 20,
  "by_split": {
    "unsplit": 20
  },
  "by_task_type": {
    "rtl_bug_review": 5
  },
  "by_design_family": {
    "counter": 3
  },
  "claim_levels": {
    "correctness": {
      "suggestion_only": 12,
      "verified": 0
    }
  },
  "duplicate_ids": []
}
```

Error cases:

- input file missing,
- malformed JSON,
- no readable rows.

Exit codes:

- `0` when inspection succeeds,
- `1` when inspection cannot run.

### 10.3 Split dataset

- Name: Split Dataset
- Method: CLI
- Path: `scripts/dataset/split_dataset.py`

Request:

```bash
python scripts/dataset/split_dataset.py \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/processed \
  --train-ratio 0.70 \
  --val-ratio 0.15 \
  --test-ratio 0.15 \
  --seed 7 \
  [--allow-family-overlap]
```

Response body:

```json
{
  "ok": true,
  "input_rows": 20,
  "train_rows": 14,
  "val_rows": 3,
  "test_rows": 3,
  "output_files": {
    "train": "data/processed/train.jsonl",
    "val": "data/processed/val.jsonl",
    "test": "data/processed/test.jsonl",
    "summary": "data/processed/split_summary.json"
  }
}
```

Error cases:

- input validation fails,
- ratios do not sum to 1.0,
- output directory cannot be created,
- no valid rows to split,
- design family overlap would occur without explicit override,
- output validation fails.

Exit codes:

- `0` when split succeeds,
- `1` when split fails.

### 10.4 Convert LLM draft

- Name: Convert LLM Draft
- Method: CLI
- Path: `scripts/dataset/convert_llm_draft.py`

Request:

```bash
python scripts/dataset/convert_llm_draft.py \
  --input data/drafts/example_llm_draft.jsonl \
  --output data/processed/converted_v0.1.jsonl
```

Response body:

```json
{
  "ok": true,
  "input_rows": 10,
  "accepted_rows": 8,
  "rejected_rows": 2,
  "output": "data/processed/converted_v0.1.jsonl"
}
```

Behavior:

- Load draft rows.
- Normalize missing optional fields where safe.
- Validate every row.
- Write only accepted rows.
- Write rejected rows to a sidecar file:

```text
data/processed/converted_v0.1.rejected.jsonl
```

Error cases:

- input file missing,
- malformed JSON,
- all rows rejected,
- output path unwritable.

### 10.5 Python validation function

- Name: `validate_dataset_file`
- Method: Python function
- Path: `scripts/dataset/validation.py`

Signature:

```python
def validate_dataset_file(path: Path, strict: bool = False) -> ValidationReport:
    ...
```

Return model:

```python
@dataclass
class ValidationReport:
    ok: bool
    rows: int
    errors: list[ValidationMessage]
    warnings: list[ValidationMessage]
    summary: dict[str, Any]
```

Error cases:

- Should not raise for row-level validation errors.
- Should raise only for programmer errors.
- File not found should be represented in `errors`.

## 11. Edge cases

Codex must handle:

- Empty JSONL file.
- Blank lines in JSONL.
- Trailing whitespace.
- Malformed JSON on one line.
- Duplicate row IDs.
- Wrong message role order.
- Missing assistant content.
- User content is a string instead of object.
- Assistant content is a string instead of object.
- Unknown `task_type`.
- Row `task_family` does not match user `task_type`.
- User `task_type` does not match answer `task_type`.
- Missing artifacts.
- All artifact fields null.
- `rtl_code` present but empty string.
- Missing `claim_levels`.
- Legacy `claim_level` present without `claim_levels`.
- Claim says "power reduced" with no power evidence.
- Claim says "area improved" with no synthesis evidence.
- Claim says "verified" with no simulation/equivalence evidence.
- Patch suggested but `functional_risk` empty.
- Reset behavior changed but reset risk is empty.
- Verification plan missing lint/compile.
- `split_dataset.py` receives ratios that sum to 0.99 or 1.01.
- Dataset has too few design families for a clean family-level split.
- Dataset contains `review_status: "draft"` rows.
- Public dataset row has missing license.
- Tool check object has unknown status.
- Windows path separators.
- Unicode signal names or comments in RTL.
- Very long RTL code fields.
- Null values in optional fields.
- Existing output files in `data/processed/`.
- Output directory does not exist.

## 12. Testing plan

### 12.1 Unit tests

Add tests for:

- `load_jsonl` handles blank lines.
- `load_jsonl` reports malformed JSON with line number.
- top-level required fields validation.
- task schema validation.
- answer schema validation.
- enum validation.
- claim-level validation.
- claim-safety phrase detection.
- duplicate ID detection.
- design family overlap detection.
- split ratio validation.

### 12.2 Integration tests

Add tests that run the scripts through subprocess:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl --json
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir <tmpdir> --seed 7
```

Validate that:

- exit code is correct,
- expected files are created,
- output JSONL files are valid,
- split summary JSON is valid,
- no design family overlap occurs by default.

### 12.3 UI tests

Not applicable. There is no UI.

### 12.4 Manual checks

A developer should manually run:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
python scripts/dataset/validate_dataset.py --input data/processed/train.jsonl --strict
python scripts/dataset/validate_dataset.py --input data/processed/val.jsonl --strict
python scripts/dataset/validate_dataset.py --input data/processed/test.jsonl --strict
```

Then run test suite:

```bash
python -m pytest tests/dataset
```

If there is a repo-wide lint/typecheck command, run it too.

## 13. Definition of done

The task is complete only when:

- `docs/specs/dataset-foundation-v0.1.md` exists.
- Dataset folders exist with `.gitkeep` where needed.
- Schema files exist and match validator expectations.
- `data/golden/golden_v0.1.jsonl` contains at least 20 valid seed rows.
- Validator CLI works and fails correctly on invalid rows.
- Inspector CLI prints useful dataset statistics.
- Split CLI writes train/val/test JSONL files and validates them.
- Claim-safety checks catch unsupported power, area, activity, and correctness claims.
- Public dataset adapter skeletons exist.
- LLM conversion contract documentation exists.
- Dataset guidelines documentation exists.
- Tests cover validation, claim safety, inspection, and splitting.
- `python -m pytest tests/dataset` passes.
- Existing repo behavior is not broken.
- No private RTL, tokens, model weights, logs, or local paths are committed.
- No unrelated files are changed.
- The implementation matches this spec.

## 14. Codex implementation instructions

Implement this spec.

Work only on the dataset foundation for `RTLSpecializer`.

Do not implement training, inference, QLoRA, DoRA, DPO, model serving, web UI, or external LLM API calls in this task.

Do not change unrelated files.

Follow the existing project patterns. If the repo is minimal, use a simple Python standard-library implementation.

Do not introduce new dependencies unless necessary. Prefer standard library validation code over adding `jsonschema`. If you add a dependency, update the correct dependency file and explain why.

Create or update the files listed in section 8.

The implementation must include:

- schemas,
- dataset docs,
- seed golden dataset,
- validator CLI,
- inspector CLI,
- splitter CLI,
- LLM draft conversion contract,
- public dataset adapter skeletons,
- tests.

Keep the dataset examples synthetic or public-safe. Do not include company/private RTL or private tool logs.

Use conservative RTL language in every seed answer. Do not claim measured power, area, activity, or correctness results unless the row includes corresponding tool evidence.

After implementation, run:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
python scripts/dataset/inspect_dataset.py --input data/golden/golden_v0.1.jsonl
python scripts/dataset/split_dataset.py --input data/golden/golden_v0.1.jsonl --output-dir data/processed --seed 7
python -m pytest tests/dataset
```

If the repo has existing lint or typecheck commands, run those too.

Summarize:

- changed files,
- commands run,
- test results,
- any tradeoffs,
- any intentionally deferred items.
