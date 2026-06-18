# Feature Spec: Dataset Release Hardening v0.1

## 1. Goal

Harden the dataset release assembly workflow so release outputs have clearer leakage semantics, stricter input failure behavior, and better test coverage.

The current release builder is useful and mostly complete. It creates release directories, writes train/val/test JSONL files, writes rejected rows, writes stats, writes a dataset card, and writes a manifest with SHA-256 hashes. However, two important semantics need tightening before release outputs become the handoff point for evaluation or training:

1. `--allow-source-overlap` is accepted by the CLI and stored in config, but no source-overlap check is implemented.
2. Duplicate artifact fingerprints are rejected before splitting, which is stricter than the intended leakage rule and does not report whether a duplicate would cross train/val/test boundaries.

This hardening pass should make release assembly behavior match the spec more precisely and make release reports more trustworthy.

## 2. Non-goals

Do not build:

- Model training.
- Evaluation harnesses.
- QLoRA, DoRA, DPO, or inference scripts.
- External LLM calls.
- Automatic public dataset downloads.
- EDA execution.
- Web UI.
- Dataset publishing/upload.
- New schema versions.
- Private/company RTL ingestion.

## 3. Assumptions

- Keep `dataset_v0.1`, `rtl_task_v0.1`, and `rtl_answer_v0.1`.
- Keep the release output layout introduced by `dataset-release-assembly-v0.1`.
- Keep Python standard-library only.
- Keep `data/golden/golden_v0.1.jsonl` as the default smoke-test input.
- Existing release builder code should be hardened, not rewritten from scratch.
- Release outputs remain local files only.

## 4. User stories

- As a trainer, I want source and artifact leakage checks to be explicit, so that train/val/test splits are trustworthy.
- As an evaluator, I want duplicate artifact warnings or rejections to explain whether leakage crosses splits, not merely whether a duplicate exists somewhere.
- As a maintainer, I want CLI flags to have real behavior, so that `--allow-source-overlap` is not misleading.
- As a dataset reviewer, I want invalid input files to fail release assembly clearly, so that a release is not built from a corrupted or partially invalid input.
- As a project lead, I want release stats to distinguish duplicate IDs, duplicate rows, duplicate artifacts, family overlap, and source overlap.

## 5. UX / UI requirements

No graphical UI.

The existing CLI remains:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1 \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7 \
  --json
```

Supported options remain:

```text
--release-name <name>
--input <jsonl>               repeatable
--output-dir <dir>
--train-ratio <float>
--val-ratio <float>
--test-ratio <float>
--seed <int>
--allow-family-overlap
--allow-source-overlap
--min-rows <int>
--strict
--json
```

### UX behavior changes

- If an input file fails strict validation, release assembly must fail by default.
- If duplicate artifact fingerprints occur only inside one split, the release may pass but must report the duplicate in `stats.json` and warnings.
- If duplicate artifact fingerprints occur across train/val/test splits, release assembly must fail unless the relevant overlap escape hatch is explicitly enabled.
- `--allow-source-overlap` must control whether the same source enum may appear in multiple splits.
- Output JSON and text summaries must include warnings when non-fatal duplicate artifacts or source overlap are allowed.

## 6. Functional requirements

### FR-1: Make strict input validation failures fatal

Currently, `load_release_inputs` collects strict validation failures as warnings. Update release assembly so that if any input file fails `validate_dataset_file(path, strict=True)`, the release fails.

Behavior:

- Continue loading rows where possible so rejected sidecar rows can still be written.
- Add validation failures to top-level `errors`, not only `warnings`.
- Return exit code `1`.
- Do not report `ok: true` when any input file failed strict validation.

### FR-2: Implement source-overlap checking

Define source overlap as the same `source` enum appearing in more than one release split.

Default behavior:

- Reject/fail the release when a source appears in more than one of `train`, `val`, or `test`.
- Include a clear error such as:

```text
source appears in multiple splits: public_verilog_eval ['train', 'test']
```

When `--allow-source-overlap` is passed:

- Do not fail for source overlap.
- Record overlaps in `stats.json` under `duplicate_leakage_checks.source_overlaps`.
- Add a warning to the result.

Rationale: source overlap is strict and may be too restrictive for tiny datasets, but the CLI flag must have real behavior.

### FR-3: Refine duplicate artifact fingerprint semantics

Change artifact fingerprint handling from pre-split global rejection to post-split leakage checking.

Required behavior:

- Compute artifact fingerprints for accepted rows before splitting.
- Preserve a mapping from fingerprint to row IDs and later assigned splits.
- After splitting, detect duplicate artifact fingerprints.
- If all rows sharing an artifact fingerprint are in the same split, do not fail the release by default.
- If rows sharing an artifact fingerprint appear in multiple splits, fail the release unless `--allow-family-overlap` is passed.
- Always report duplicate artifact fingerprints in `stats.json`.

Recommended stats shape:

```json
{
  "duplicate_leakage_checks": {
    "duplicate_row_ids": 0,
    "duplicate_row_fingerprints": 0,
    "duplicate_artifact_fingerprints": 1,
    "artifact_fingerprint_overlaps": {
      "<sha256>": {
        "rows": ["row_a", "row_b"],
        "splits": ["train", "test"]
      }
    },
    "source_overlaps": {},
    "family_overlaps": {}
  }
}
```

### FR-4: Keep duplicate row ID and duplicate full-row fingerprint rejection pre-split

Duplicate row IDs and duplicate full-row fingerprints should remain release eligibility failures before splitting.

Rules:

- Duplicate row ID: reject the later row.
- Duplicate full-row fingerprint: reject the later row.
- These rows must appear in `rejected_rows.jsonl`.
- Release may still succeed if enough accepted rows remain and no fatal errors are present.

### FR-5: Improve leakage summary

Expand `duplicate_leakage_checks` in `stats.json` to include:

```json
{
  "duplicate_row_ids": 0,
  "duplicate_row_fingerprints": 0,
  "duplicate_artifact_fingerprints": 0,
  "family_overlap_allowed": false,
  "source_overlap_allowed": false,
  "family_overlaps": {},
  "source_overlaps": {},
  "artifact_fingerprint_overlaps": {}
}
```

The summary must include enough row IDs and split names to debug leakage.

### FR-6: Add release result warnings

When overlap is allowed by flag, add warnings to the top-level result.

Examples:

```text
source overlap allowed for handwritten_golden across ['train', 'val', 'test']
artifact fingerprint overlap allowed for <hash> across ['train', 'test']
```

Warnings must appear in JSON output and text output.

### FR-7: Normalize release output metadata paths

Ensure manifest and stats paths use deterministic POSIX-style relative paths inside the release directory where possible.

The existing manifest already stores file paths like `train.jsonl`, which is good. Keep this behavior. Avoid absolute paths inside release metadata except for input files if already intentionally recorded.

### FR-8: Add tests

Add or update tests under `tests/dataset/test_release.py`.

Required tests:

- `--allow-source-overlap` has observable behavior.
- Default source overlap fails when the same source appears in multiple splits.
- Source overlap passes with warning when `allow_source_overlap=True`.
- Duplicate artifact fingerprint in the same split is reported but does not fail.
- Duplicate artifact fingerprint across splits fails by default.
- Duplicate artifact fingerprint across splits passes with warning when overlap is explicitly allowed.
- Strict input validation failure makes the release fail.
- Duplicate row ID and duplicate full-row fingerprint are still rejected pre-split.
- Stats JSON includes `family_overlaps`, `source_overlaps`, and `artifact_fingerprint_overlaps`.
- Existing golden release smoke test still passes, using flags if necessary for tiny-dataset source overlap.

### FR-9: Update docs

Update:

```text
docs/dataset/release_workflow.md
docs/dataset/dataset_guidelines.md
```

Docs must explain:

- source overlap default behavior,
- when to use `--allow-source-overlap`,
- difference between duplicate row, duplicate artifact, and cross-split artifact leakage,
- why duplicate artifacts inside one split are less dangerous than cross-split duplicates,
- tiny datasets may need explicit source-overlap allowance for smoke-test releases.

## 7. Technical requirements

### 7.1 Architecture

Keep the current architecture:

```text
build_dataset_release.py
  -> release.py
  -> validate_dataset_file
  -> split_rows
  -> manifest/stats/card
```

Do not duplicate large validation logic.

### 7.2 Suggested implementation approach

- Keep pre-split pass for eligibility, duplicate IDs, and full-row fingerprints.
- Move artifact fingerprint cross-split leakage check after `split_rows` assigns rows.
- Add helper functions:

```python
def collect_split_membership(split: dict[str, list[dict]]) -> dict[str, set[str]]:
    ...

def find_source_overlaps(split: dict[str, list[dict]]) -> dict[str, list[str]]:
    ...

def find_artifact_fingerprint_overlaps(split: dict[str, list[dict]]) -> dict[str, dict]:
    ...
```

### 7.3 Security

- Continue treating dataset rows as untrusted data.
- Do not execute RTL, logs, or embedded code.
- Do not shell out.
- Do not download data.
- Do not add private/company data.

### 7.4 Dependencies

Use standard library only.

## 8. Files likely involved

Likely modify:

```text
scripts/dataset/release.py
tests/dataset/test_release.py
docs/dataset/release_workflow.md
docs/dataset/dataset_guidelines.md
```

Optionally modify:

```text
scripts/dataset/build_dataset_release.py
README.md
```

Do not modify unrelated files.

## 9. Data model

No schema migration.

Only `stats.json` is expanded with more precise leakage fields.

## 10. API contract

The CLI contract remains the same.

Example smoke-test command may need explicit source-overlap allowance for single-source tiny releases:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name release_v0.1 \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir data/releases \
  --seed 7 \
  --allow-source-overlap \
  --json
```

If source overlap is not allowed and all rows come from `handwritten_golden`, the release should fail with a clear source-overlap error once rows are split into multiple splits.

## 11. Edge cases

Handle:

- single-source tiny golden release,
- multi-source release where each source is isolated by split,
- same source appearing in multiple splits,
- same artifact appearing twice in the same split,
- same artifact appearing in train and test,
- duplicate row ID,
- duplicate full-row fingerprint,
- invalid input JSONL mixed with valid rows,
- empty accepted set,
- `--allow-family-overlap` and `--allow-source-overlap` used together.

## 12. Testing plan

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run:

```bash
python scripts/dataset/build_dataset_release.py \
  --release-name test_release \
  --input data/golden/golden_v0.1.jsonl \
  --output-dir /tmp/rtl_specializer_releases \
  --seed 7 \
  --allow-source-overlap \
  --json
```

Validate the generated train/val/test release files.

## 13. Definition of done

Done only when:

- Input validation failures make release assembly fail.
- `--allow-source-overlap` has real behavior.
- Source overlap is detected and reported.
- Artifact fingerprint duplicates are reported with split membership.
- Cross-split artifact fingerprint leakage fails unless explicitly allowed.
- Same-split artifact duplicates are reported but do not fail by default.
- Duplicate row ID and full-row fingerprint rejection still works.
- Stats JSON includes expanded leakage summary.
- Docs explain release leakage semantics.
- Tests cover the new behavior.
- Existing release smoke test still passes with appropriate flags.

## 14. Codex implementation instructions

Implement this focused release hardening spec.

Do not add training, evaluation harnesses, inference, external LLM calls, downloads, EDA execution, or schema changes.

Keep the current release builder architecture.

Use standard-library Python only.

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run the release builder against the golden dataset with `--allow-source-overlap` and validate generated train/val/test files.

After finishing, commit and push. Summarize changed files, commands run, test results, and tradeoffs.