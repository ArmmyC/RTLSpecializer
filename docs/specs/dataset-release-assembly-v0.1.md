# Feature Spec: Dataset Release Assembly v0.1

## 1. Goal

Build a reproducible dataset release assembly workflow for `RTLSpecializer`.

The repo can now create high-quality golden rows, import public-safe local draft rows, prepare review packets, and promote edited public rows into validated candidates. The next highest-value step is to assemble trusted rows into a versioned release directory that can be used by future evaluation and fine-tuning scripts.

This feature creates a deterministic local release builder:

```text
golden reviewed rows
+ promoted validated public rows
+ optional extra validated JSONL files
  -> release validation
  -> leakage checks
  -> train/val/test split
  -> dataset card
  -> manifest with hashes
  -> release directory
```

The release output must be explicit, reproducible, auditable, and safe. It must not train a model. It only prepares dataset artifacts.

## 2. Non-goals

Do not build:

- Model training.
- QLoRA, DoRA, DPO, or inference scripts.
- External LLM calls.
- Automatic public dataset downloads.
- EDA execution.
- Web UI.
- Dataset upload/publishing.
- New schema versions.
- Private/company RTL ingestion.
- Automatic license approval.

## 3. Assumptions

- The repo keeps `dataset_v0.1`, `rtl_task_v0.1`, and `rtl_answer_v0.1`.
- Dataset release version starts at `release_v0.1`.
- Inputs are local JSONL files that already follow `dataset_v0.1`.
- `data/golden/golden_v0.1.jsonl` is the default golden input.
- Promoted public rows are optional at first.
- Only rows with `review_status` equal to `validated` or `reviewed` may enter a release.
- Release output should be stored under `data/releases/<release-name>/`.
- Python standard library is enough.

## 4. User stories

- As a model trainer, I want one deterministic release directory, so that training scripts do not consume random draft files.
- As an evaluator, I want leakage checks by design family and row fingerprints, so that validation and test sets are meaningful.
- As a maintainer, I want a dataset card and manifest, so that I can audit what went into a release.
- As a reviewer, I want rejected or draft rows excluded automatically, so that unsafe rows do not enter training.
- As a project lead, I want release statistics by source, task type, design family, and claim level, so that dataset coverage is visible before training.

## 5. UX / UI requirements

No graphical UI.

Add CLI:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1 \
  --input data/golden/golden_v0.1.jsonl \
  --input data/processed/public_validated_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7
```

Supported options:

```text
--release-name <name>
--input <jsonl>               repeatable
--output-dir <dir>
--train-ratio <float>         default 0.70
--val-ratio <float>           default 0.15
--test-ratio <float>          default 0.15
--seed <int>                  default 7
--allow-family-overlap
--allow-source-overlap
--min-rows <int>              default 1
--strict
--json
```

### Success state

```text
Dataset release built.

Release: release_v0.1
Input files: 2
Input rows: 75
Accepted rows: 75
Rejected rows: 0
Train rows: 52
Val rows: 11
Test rows: 12
Output: data/releases/release_v0.1
```

Exit code: `0`.

### Failure state

```text
Dataset release failed.

Errors:
- duplicate row id: public_verilog_eval_counter_001
- design family appears in multiple splits: counter
```

Exit code: `1`.

### JSON output

When `--json` is set, print:

```json
{
  "ok": true,
  "release_name": "release_v0.1",
  "input_files": 2,
  "input_rows": 75,
  "accepted_rows": 75,
  "rejected_rows": 0,
  "train_rows": 52,
  "val_rows": 11,
  "test_rows": 12,
  "output_dir": "data/releases/release_v0.1",
  "errors": [],
  "warnings": []
}
```

## 6. Functional requirements

### FR-1: Add release builder CLI

Create:

```text
scripts/dataset/build_dataset_release.py
```

It must:

- accept repeatable `--input` JSONL files,
- validate every input file using `validate_dataset_file(..., strict=True)`,
- load all rows,
- reject rows not eligible for release,
- deduplicate rows by ID and fingerprint,
- split eligible rows into train/val/test,
- write release files,
- write a report and manifest,
- return clear exit codes.

### FR-2: Add reusable release module

Create:

```text
scripts/dataset/release.py
```

Suggested functions:

```python
def row_fingerprint(row: dict) -> str:
    ...

def load_release_inputs(paths: list[Path]) -> tuple[list[dict], list[dict]]:
    ...

def build_release(rows: list[dict], config: ReleaseConfig) -> ReleaseResult:
    ...
```

Use deterministic JSON serialization with sorted keys for hashes.

### FR-3: Release eligibility gates

A row may enter a release only if:

- `dataset_version == "dataset_v0.1"`,
- `review_status` is `validated` or `reviewed`,
- `split` is `unsplit`, `train`, `val`, or `test`,
- `source` is an allowed source enum,
- `license` is non-empty and not `unknown`, `uncertain`, or `todo`,
- validation passes under strict mode,
- assistant answer is not a generic import stub,
- no unsupported claims are detected by existing validation,
- no private/proprietary source marker is present.

Rows failing gates must be written to:

```text
rejected_rows.jsonl
```

Rejected rows must include:

```json
{
  "id": "...",
  "reason": "release eligibility failed",
  "errors": ["..."],
  "row": {}
}
```

### FR-4: Duplicate and leakage checks

Detect and reject:

- duplicate row IDs,
- duplicate full-row fingerprints,
- duplicate task artifact fingerprints where the same RTL/report appears in multiple splits,
- same `design_family` in more than one split unless `--allow-family-overlap` is passed.

The builder should split by `design_family` by default, similar to the existing splitter.

### FR-5: Deterministic splitting

The release builder must produce train/val/test files using the requested ratios and seed.

Rules:

- Default ratios are 0.70 / 0.15 / 0.15.
- Ratios must sum to 1.0 within a small tolerance.
- Split by design family by default.
- Preserve row content except for setting `split` to `train`, `val`, or `test` in release files.
- Do not mutate source files.
- Repeat runs with the same inputs and seed must produce identical outputs.

### FR-6: Release output layout

Create:

```text
data/releases/<release-name>/
  train.jsonl
  val.jsonl
  test.jsonl
  rejected_rows.jsonl
  dataset_card.md
  manifest.json
  stats.json
```

Optional:

```text
  all_accepted.unsplit.jsonl
```

### FR-7: Manifest JSON

`manifest.json` must include:

```json
{
  "release_name": "release_v0.1",
  "dataset_version": "dataset_v0.1",
  "schema_versions": {
    "task": "rtl_task_v0.1",
    "answer": "rtl_answer_v0.1"
  },
  "created_by": "build_dataset_release.py",
  "seed": 7,
  "ratios": {
    "train": 0.7,
    "val": 0.15,
    "test": 0.15
  },
  "input_files": [],
  "files": {
    "train": {"path": "train.jsonl", "sha256": "...", "rows": 0},
    "val": {"path": "val.jsonl", "sha256": "...", "rows": 0},
    "test": {"path": "test.jsonl", "sha256": "...", "rows": 0},
    "rejected": {"path": "rejected_rows.jsonl", "sha256": "...", "rows": 0}
  }
}
```

Use SHA-256 of file bytes.

### FR-8: Stats JSON

`stats.json` must include:

- row counts by split,
- row counts by source,
- row counts by task type,
- row counts by design family,
- row counts by review status,
- claim-level distribution by domain,
- rejected row counts by reason,
- duplicate/leakage check summary.

### FR-9: Dataset card

`dataset_card.md` must include:

- release name,
- purpose,
- included sources,
- row counts,
- task distribution,
- design family distribution,
- claim-level policy summary,
- provenance and license warning,
- known limitations,
- commands used to rebuild,
- note that no model training happened,
- note that no EDA tools were run by release assembly.

### FR-10: Release validation

After writing release files, validate:

```bash
python scripts/dataset/validate_dataset.py --input data/releases/<release-name>/train.jsonl --strict
python scripts/dataset/validate_dataset.py --input data/releases/<release-name>/val.jsonl --strict
python scripts/dataset/validate_dataset.py --input data/releases/<release-name>/test.jsonl --strict
```

The CLI may call `validate_dataset_file` directly. If any output fails validation, the release build must fail.

### FR-11: Add tests

Add tests under:

```text
tests/dataset/test_release.py
```

Required tests:

- release builder accepts golden dataset and writes release layout,
- release files validate under strict mode,
- manifest contains hashes and row counts,
- stats contain task/source/design-family counts,
- duplicate row IDs are rejected,
- draft rows are rejected,
- unknown/uncertain license is rejected,
- repeated run with same seed is deterministic,
- design family overlap is prevented by default,
- CLI `--json` output is parseable.

### FR-12: Update docs

Create or update:

```text
docs/dataset/release_workflow.md
docs/dataset/dataset_guidelines.md
README.md
```

Docs must explain:

- what a dataset release is,
- difference between golden, draft, promoted, and release rows,
- how to build a release,
- why draft rows are excluded,
- why family-level split isolation matters,
- how to read manifest hashes and stats,
- release assembly does not prove correctness or train a model.

## 7. Technical requirements

### 7.1 Architecture

Add release assembly after promotion:

```text
validated/reviewed JSONL inputs
  -> release eligibility gates
  -> dedupe and leakage checks
  -> deterministic family split
  -> train/val/test release files
  -> manifest/stats/card
```

Reuse existing utilities where possible:

- `load_jsonl`,
- `write_jsonl`,
- `validate_dataset_file`,
- existing split logic if cleanly reusable.

Do not duplicate large validation logic unnecessarily.

### 7.2 Hashing

Use SHA-256.

Hash files from bytes after writing.

Row fingerprints should use:

```python
json.dumps(row, sort_keys=True, separators=(",", ":"))
```

Artifact fingerprints should hash concatenated non-empty artifacts from user task content.

### 7.3 Security

- Treat all dataset rows as untrusted data.
- Do not execute RTL or tool logs.
- Do not shell out.
- Do not download data.
- Do not include private/company RTL.
- Do not write outside the requested release directory.

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
hashlib
random
sys
```

## 8. Files likely involved

Create:

```text
scripts/dataset/build_dataset_release.py
scripts/dataset/release.py
docs/dataset/release_workflow.md
tests/dataset/test_release.py
```

Modify:

```text
docs/dataset/dataset_guidelines.md
README.md
```

Do not modify unrelated files.

## 9. Data model

No database.

Release files are JSONL plus JSON/Markdown metadata.

### Release row

Same `dataset_v0.1` row format. Only `split` changes in the emitted train/val/test files.

### Rejected release row

```json
{
  "id": "...",
  "reason": "duplicate row id",
  "errors": ["duplicate row id first seen in ..."],
  "row": {}
}
```

### Manifest file record

See FR-7.

## 10. API contract

### Build Dataset Release

- Name: Build Dataset Release
- Method: CLI
- Path: `scripts/dataset/build_dataset_release.py`

Request:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1 \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7 \
  --json
```

Response:

```json
{
  "ok": true,
  "release_name": "release_v0.1",
  "input_files": 1,
  "input_rows": 20,
  "accepted_rows": 20,
  "rejected_rows": 0,
  "train_rows": 14,
  "val_rows": 3,
  "test_rows": 3,
  "output_dir": "data/releases/release_v0.1",
  "errors": [],
  "warnings": []
}
```

Error cases:

- missing input file,
- malformed JSONL,
- no accepted rows,
- invalid ratios,
- duplicate IDs,
- duplicate fingerprints,
- draft/rejected row,
- uncertain license,
- validation failure,
- output validation failure.

Exit codes:

- `0` on success,
- `1` on failure.

## 11. Edge cases

Handle:

- one input file,
- multiple input files,
- empty input file,
- malformed input file,
- duplicate IDs across files,
- duplicate row content with different IDs,
- draft rows mixed with reviewed rows,
- rejected rows mixed with reviewed rows,
- unknown license,
- same design family across multiple inputs,
- too few design families for exact ratio,
- existing release directory,
- Windows path separators,
- Unicode signal names/comments,
- very small dataset.

## 12. Testing plan

### Unit tests

Test:

- row fingerprint stability,
- artifact fingerprint stability,
- release eligibility gates,
- stats generation,
- manifest hash creation.

### Integration tests

Run:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name test_release \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir <tmpdir>/releases \
  --seed 7 \
  --json
```

Then validate:

```bash
python scripts/dataset/validate_dataset.py --input <tmpdir>/releases/test_release/train.jsonl --strict
python scripts/dataset/validate_dataset.py --input <tmpdir>/releases/test_release/val.jsonl --strict
python scripts/dataset/validate_dataset.py --input <tmpdir>/releases/test_release/test.jsonl --strict
```

### Manual checks

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

## 13. Definition of done

The task is complete only when:

- Release builder CLI exists.
- Release builder accepts one or more input JSONL files.
- Only validated/reviewed rows enter release files.
- Draft/rejected/uncertain-license rows are rejected.
- Duplicate IDs and duplicate fingerprints are handled.
- Train/val/test files are deterministic and validate under strict mode.
- Manifest includes file hashes and row counts.
- Stats JSON includes useful coverage counts.
- Dataset card is written.
- Tests cover release assembly and failure cases.
- Docs explain release workflow.
- No external services, downloads, EDA execution, or private data are introduced.

## 14. Codex implementation instructions

Implement this spec exactly.

Focus only on dataset release assembly.

Do not implement training, inference, external LLM calls, downloads, EDA execution, or schema version changes.

Use standard-library Python only.

Reuse existing dataset validation and JSONL utilities.

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run the new release builder against the golden dataset and validate the generated release train/val/test files.

After finishing, commit and push. Summarize changed files, commands run, test results, and tradeoffs.