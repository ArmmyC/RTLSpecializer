# RTL answer audit and repair

This workflow audits standalone `rtl_answer.v0.1` teacher-answer files and writes conservative repaired copies. It is for draft teacher and synthetic distillation data only.

It does not run RTL, call an LLM, train a model, mark rows reviewed, approve rows, or promote anything to `data/golden`.

## What The Validator Checks

The validator reads:

- JSON wrappers such as `{ "answers": [...] }`
- JSON arrays of answer rows
- JSONL files with one answer object per line
- single JSON answer objects

It skips generated chat/train rows by default. Those rows contain `messages` and should not be repaired through the standalone answer-file workflow unless a future tool explicitly opts into that behavior.

Checks include:

- parseable JSON or JSONL
- `schema_version: rtl_answer.v0.1`
- required answer fields such as `source_id`, `issue_summary`, `claim_levels`, `evidence_used`, and `limitations`
- duplicate `source_id` values
- conservative `claim_levels.correctness` when simulation/equivalence evidence is absent
- `area`, `activity`, and `power` set to `insufficient_evidence` when reports are absent
- unsupported wording such as `passed simulation`, `passed lint`, `synthesized successfully`, `area improved`, `power improved`, `timing met`, or `equivalent by formal`
- generic labels inside `issue_summary[*].evidence.signal_names`
- missing `tool_checks` evidence when limitations discuss null or not-run tool checks
- missing limitations saying tool checks were not run when tool evidence is absent
- empty `issue_summary` for candidate-bug rows
- invalid severity values
- copied full task objects inside answers
- suspicious mismatch between source-id mutation type and issue text

If `--tasks` is provided, the validator also reports answer rows without matching tasks and task rows without matching answers.

## Safe Automatic Repairs

The repair script writes patched files to a new output directory by default. It preserves originals unless `--in-place` is explicitly used.

Allowed automatic repairs are intentionally narrow:

- normalize `rtl_answer_v0.1` to `rtl_answer.v0.1`
- deduplicate list fields such as `evidence_used`, `functional_risk`, `verification_plan`, `limitations`, `signal_names`, and `hardware_resources_involved`
- add `tool_checks` to `evidence_used` when limitations say tool checks are null or were not run
- normalize synthetic candidate-bug `evidence_used` toward the expected task artifacts
- move generic labels out of `signal_names` and into `space_reasoning.hardware_resources_involved`
- downgrade unsupported claim levels when tool evidence is absent
- add a conservative no-tool-checks limitation when needed
- replace `verified by text inspection` with `reviewed by text inspection`

Each change is recorded with file path, `source_id`, field, old value, new value, reason, and fix type.

## Manual Review Only

The repair script flags but does not auto-fix rows that need semantic judgment:

- source-id mutation type disagrees with the actual issue text
- line ranges are missing or cannot be inferred safely
- signal names appear wrong but cannot be corrected from supplied data
- candidate/reference relationship is unclear
- an answer claims a candidate bug without candidate RTL or mutation metadata
- an answer appears to copy the full task object
- any issue requiring RTL semantics beyond obvious text cleanup

## Example Commands

Validate before repair:

```bash
python scripts/dataset/validate_rtl_answer_files.py \
  --input-dir data/answers/teacher_returns \
  --glob "*rtl_answer*v0_1*.json*" \
  --output-md data/reports/validation/rtl_answer_validation_before_repair.md \
  --output-json data/reports/validation/rtl_answer_validation_before_repair.json \
  --strict \
  --json
```

Repair into a separate output directory:

```bash
python scripts/dataset/repair_rtl_answer_files.py \
  --input-dir data/answers/teacher_returns \
  --glob "*rtl_answer*v0_1*.json*" \
  --output-dir data/answers/repaired \
  --report-md data/reports/repair/repair_report.md \
  --report-json data/reports/repair/repair_report.json \
  --json
```

Validate patched files:

```bash
python scripts/dataset/validate_rtl_answer_files.py \
  --input-dir data/answers/repaired \
  --glob "*rtl_answer*v0_1*.json*" \
  --output-md data/reports/validation/validation_after_repair.md \
  --output-json data/reports/validation/validation_after_repair.json \
  --strict \
  --json
```

## Outputs

Patched files and reports are written under:

```text
data/answers/repaired/
```

Recommended reports:

```text
data/reports/validation/rtl_answer_validation_before_repair.md
data/reports/validation/rtl_answer_validation_before_repair.json
data/reports/repair/repair_report.md
data/reports/repair/repair_report.json
data/reports/validation/validation_after_repair.md
data/reports/validation/validation_after_repair.json
```

Repaired rows remain draft/synthetic teacher-distillation data. They are not golden, not approved, and not human-reviewed.
