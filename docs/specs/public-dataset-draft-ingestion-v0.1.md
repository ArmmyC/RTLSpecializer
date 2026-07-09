# Feature Spec: Public Dataset Draft Ingestion v0.1

## 1. Goal

Build a local-only public dataset draft ingestion pipeline for `RTLSpecializer`.

The current repo has a strong seed dataset foundation: schema validation, claim-safety validation, evidence-status validation, golden examples, processed splits, and adapter skeletons. The next highest-value step is to ingest public-safe RTL benchmark artifacts into draft rows without downloading anything, executing RTL, or calling an LLM.

This feature should convert explicitly supplied local public dataset files into conservative `dataset_v0.1` JSONL draft rows that can later be reviewed, LLM-augmented, validated, and promoted.

The pipeline must support two levels:

1. A generic manifest adapter that works immediately with a documented local JSONL manifest format.
2. Thin local-directory adapters for VerilogEval-style, RTLLM-style, and RTLFixer-style folders where discovery can be deterministic and conservative.

The output must be draft-only by default:

```text
review_status: draft
split: unsplit
```

The generated rows must validate structurally, but they must not be training-ready unless a later human or LLM review process upgrades them to `validated` or `reviewed`.

## 2. Non-goals

Do not build:

- Model fine-tuning.
- Runtime inference.
- External LLM calls.
- Automatic internet downloads.
- Git submodule setup for public datasets.
- EDA execution, simulation, synthesis, equivalence, or toggle counting.
- Full public dataset normalization for every upstream format variation.
- Promotion from `draft` to `validated` or `reviewed`.
- Human review UI.
- Private/company RTL ingestion.
- New schema versions.

## 3. Assumptions

- The repo keeps `dataset_v0.1`, `rtl_task_v0.1`, and `rtl_answer_v0.1`.
- Public dataset artifacts are supplied manually under `data/raw_public/` or another local path.
- The importer must never download data.
- The importer must never execute dataset content.
- Standard library Python is preferred.
- A manifest-based import format is acceptable as the reliable v0.1 path.
- VerilogEval, RTLLM, and RTLFixer adapters may begin as conservative local file discoverers that produce rows only when required files are clearly present.
- Generated answers are conservative draft stubs, not final training labels.
- All generated draft rows must pass `validate_dataset.py` but remain excluded from normal splitting by `review_status: draft`.

## 4. User stories

- As a dataset builder, I want to convert local public RTL artifacts into `dataset_v0.1` draft rows, so that I can scale beyond hand-written golden examples.
- As a reviewer, I want imported rows marked as draft with source and license metadata, so that I know they are not training-ready yet.
- As a maintainer, I want a manifest adapter, so that public datasets with different layouts can still be ingested through one stable contract.
- As a future LLM conversion pipeline, I want draft rows with complete `rtl_task_v0.1` objects, so that the LLM only needs to refine the answer rather than guess the artifact structure.
- As a security-conscious contributor, I want the importer to never execute or download artifacts, so that untrusted public files remain data only.

## 5. UX / UI requirements

No graphical UI.

Add CLI commands under `scripts/dataset/`.

### 5.1 Required CLI

Create:

```bash
python scripts/dataset/import_public_dataset.py \
  --adapter manifest \
  --input data/raw_public/example_manifest.jsonl \
  --output data/drafts/public_manifest_draft_v0.1.jsonl
```

Supported options:

```text
--adapter manifest|verilog_eval|rtllm|rtlfixer
--input <path>
--output <path>
--source <optional source override>
--license <optional license override>
--limit <optional max rows>
--strict
--json
```

### 5.2 Success state

Text output example:

```text
Public dataset import completed.

Adapter: manifest
Discovered examples: 12
Imported rows: 12
Rejected examples: 0
Output: data/drafts/public_manifest_draft_v0.1.jsonl
Rejected output: data/drafts/public_manifest_draft_v0.1.rejected.jsonl
```

Exit code: `0`.

### 5.3 Partial success state

If some examples are rejected but at least one row is imported:

```text
Public dataset import completed with rejections.

Adapter: manifest
Discovered examples: 12
Imported rows: 10
Rejected examples: 2
```

Exit code: `0` by default.

Exit code: `1` when `--strict` is set.

### 5.4 Failure state

If no rows can be imported:

```text
Public dataset import failed.

Errors:
- input path not found: data/raw_public/missing.jsonl
```

Exit code: `1`.

### 5.5 JSON output

When `--json` is set, print:

```json
{
  "ok": true,
  "adapter": "manifest",
  "discovered_examples": 12,
  "imported_rows": 12,
  "rejected_examples": 0,
  "output": "data/drafts/public_manifest_draft_v0.1.jsonl",
  "rejected_output": "data/drafts/public_manifest_draft_v0.1.rejected.jsonl",
  "errors": [],
  "warnings": []
}
```

## 6. Functional requirements

### FR-1: Define a generic public manifest format

Create documentation:

```text
docs/dataset/public_manifest_format.md
```

The manifest is JSONL. Each line describes one local public example.

Required fields:

```json
{
  "id": "verilog_eval_counter_001",
  "source": "public_verilog_eval",
  "license": "see_upstream",
  "design_family": "counter",
  "task_type": "rtl_bug_review",
  "user_goal": "find_correctness_bug",
  "artifacts": {
    "rtl_code_path": "examples/counter/candidate.v",
    "before_rtl_code_path": null,
    "after_rtl_code_path": null,
    "testbench_path": "examples/counter/tb.v",
    "lint_log_path": null,
    "synthesis_report_path": null,
    "toggle_report_path": null
  },
  "provenance": {
    "public_dataset_name": "VerilogEval",
    "public_dataset_url": "https://example.invalid/upstream",
    "source_commit": null,
    "notes": "Local copy supplied manually."
  }
}
```

Rules:

- Paths are relative to the manifest file directory unless absolute paths are explicitly allowed by `--allow-absolute-paths`.
- Absolute paths are rejected by default.
- Paths must not escape the manifest root through `..` unless `--allow-outside-root` is explicitly set.
- At least one artifact path must be non-null.
- Artifact files must be read as UTF-8 text.
- Missing artifact files reject that manifest row.
- The importer must not execute any artifact.

### FR-2: Implement manifest adapter

Implement `ManifestAdapter` under:

```text
scripts/dataset/adapters/manifest.py
```

It must:

- read the JSONL manifest,
- validate required manifest fields,
- resolve local artifact paths safely,
- read artifact text,
- produce `RawPublicExample` objects or a clearer richer dataclass if needed,
- convert each example into a full `dataset_v0.1` draft row.

### FR-3: Complete adapter registry

Add a registry function:

```python
def get_adapter(name: str) -> PublicDatasetAdapter:
    ...
```

Supported adapter names:

```text
manifest
verilog_eval
rtllm
rtlfixer
```

Invalid adapter names must produce a clear CLI error.

### FR-4: Generate full draft dataset rows

Each imported row must include the full dataset envelope:

```json
{
  "id": "public_verilog_eval_counter_001",
  "dataset_version": "dataset_v0.1",
  "split": "unsplit",
  "source": "public_verilog_eval",
  "license": "see_upstream",
  "design_family": "counter",
  "task_family": "rtl_bug_review",
  "created_by": "script",
  "review_status": "draft",
  "provenance": {},
  "tool_checks": {},
  "messages": []
}
```

Rules:

- `review_status` must always be `draft` for imported rows.
- `split` must always be `unsplit`.
- `created_by` must be `script`.
- Row IDs must be unique within the output file.
- Row IDs should be prefixed by source when possible.
- Source must be one of the allowed source enum values.
- License must be non-empty.

### FR-5: Generate complete `rtl_task_v0.1`

The user message must contain `rtl_task_v0.1`.

The importer must fill:

- `schema_version`,
- `domain`,
- `task_type`,
- `user_goal`,
- `design_context`,
- `artifacts`,
- `extracted_rtl_summary`,
- `constraints`,
- `assumptions`,
- `required_output`.

For v0.1, extraction may be regex-based and conservative.

At minimum, extract when visible:

- top module name,
- clock-like signals named `clk`, `clock`, or ending in `_clk`,
- reset-like signals named `rst`, `reset`, `rst_n`, `reset_n`, or ending in `_rst_n`,
- registered signals from simple `<=` assignments,
- suspected counters from signal names containing `count`, `counter`, `cnt`, or `timer`,
- suspected FSM signals from names containing `state` or `next_state`.

If extraction is uncertain, use empty lists or `null` and add an assumption.

### FR-6: Generate conservative draft `rtl_answer_v0.1`

Because this importer does not call an LLM and does not run tools, the assistant message must be a conservative draft stub.

Required answer behavior:

- `schema_version`: `rtl_answer_v0.1`
- `task_type`: match task
- `issue_summary`: one issue saying the row is imported and requires review, with evidence naming supplied artifacts
- `time_reasoning`: state that cycle/reset/latency behavior must be reviewed
- `space_reasoning`: state that area/activity claims require tools
- `safe_optimization.patch_style`: `explanation_only`
- `functional_risk`: non-empty
- `verification_plan`: include lint/compile, simulation if RTL/testbench present, synthesis if area is relevant, VCD/toggle if activity is relevant
- `claim_levels`: all conservative:

```json
{
  "correctness": "insufficient_evidence",
  "area": "insufficient_evidence",
  "activity": "insufficient_evidence",
  "power": "insufficient_evidence"
}
```

- `patch.provided`: `false`

The draft answer must never claim correctness, area, activity, or power improvement.

### FR-7: Validate generated rows

After generating rows, the CLI must validate them using `validate_dataset_file`.

- Valid rows go to the requested output JSONL.
- Invalid rows go to a sidecar rejected JSONL.
- The rejected JSONL must include source example ID, reason, and validation errors.

Output paths:

```text
<output>.jsonl
<output>.rejected.jsonl
```

If the requested output is:

```text
data/drafts/public_manifest_draft_v0.1.jsonl
```

then rejected output must be:

```text
data/drafts/public_manifest_draft_v0.1.rejected.jsonl
```

### FR-8: Implement local VerilogEval-style adapter

Implement a conservative local adapter for manually supplied VerilogEval-style folders.

The adapter should support at least one documented layout:

```text
<input>/
  manifest.jsonl
```

If `manifest.jsonl` exists, delegate to the manifest adapter.

Optionally support simple recursive discovery:

```text
<input>/<example_id>/prompt.txt
<input>/<example_id>/spec.txt
<input>/<example_id>/candidate.v
<input>/<example_id>/reference.v
<input>/<example_id>/testbench.v
<input>/<example_id>/compile.log
<input>/<example_id>/simulate.log
```

If layout is not recognized, reject with a clear message and point users to the manifest format.

### FR-9: Implement local RTLLM-style adapter

Implement a conservative local adapter for manually supplied RTLLM-style folders.

For v0.1:

- If `<input>/manifest.jsonl` exists, delegate to manifest adapter.
- Otherwise, support only a simple documented per-example folder layout.
- Do not attempt broad guessing over arbitrary folders.

### FR-10: Implement local RTLFixer-style adapter

Implement a conservative local adapter for manually supplied RTLFixer-style folders.

For v0.1:

- If `<input>/manifest.jsonl` exists, delegate to manifest adapter.
- Otherwise, support only a simple documented per-example folder layout containing broken RTL and compiler/simulation log artifacts.

### FR-11: Add tests

Add tests under `tests/dataset/` for:

- manifest adapter imports one valid manifest row,
- manifest adapter rejects missing artifact files,
- manifest adapter rejects path traversal by default,
- imported rows are `review_status: draft`, `split: unsplit`, and `created_by: script`,
- imported rows validate successfully,
- imported rows are rejected by `split_dataset.py` without `--allow-unreviewed`,
- CLI `--json` output is parseable,
- invalid adapter name fails clearly,
- duplicate imported IDs are rejected or de-duplicated deterministically,
- VerilogEval adapter delegates to manifest when `manifest.jsonl` is present.

### FR-12: Add sample manifest fixture

Create a small test fixture, not a real large dataset:

```text
tests/fixtures/public_manifest/
  manifest.jsonl
  counter_candidate.v
  counter_tb.v
```

The fixture must be synthetic and public-safe.

Do not place large public datasets in the repo.

### FR-13: Update documentation

Update or create:

```text
docs/dataset/public_dataset_sources.md
docs/dataset/public_manifest_format.md
docs/dataset/dataset_guidelines.md
```

Docs must explain:

- no automatic downloads,
- how to place local public artifacts,
- how to write a manifest,
- how to run the importer,
- why imported rows are draft-only,
- how to validate imported rows,
- how drafts can later be reviewed or LLM-refined.

## 7. Technical requirements

### 7.1 Architecture

Add these modules:

```text
scripts/dataset/import_public_dataset.py
scripts/dataset/adapters/manifest.py
```

Modify existing adapter files:

```text
scripts/dataset/adapters/__init__.py
scripts/dataset/adapters/base.py
scripts/dataset/adapters/verilog_eval.py
scripts/dataset/adapters/rtllm.py
scripts/dataset/adapters/rtlfixer.py
```

Suggested supporting helpers:

```text
scripts/dataset/rtl_extract.py
scripts/dataset/draft_rows.py
```

Keep the implementation simple and standard-library only.

### 7.2 Data flow

```text
local public artifacts
  -> manifest or adapter discovery
  -> RawPublicExample
  -> draft dataset_v0.1 row
  -> validate_dataset_file
  -> accepted JSONL + rejected JSONL
```

### 7.3 Path safety

By default:

- reject absolute artifact paths,
- reject paths that escape the manifest/input root,
- reject symlinks that resolve outside the root if practical with standard library,
- read only regular files,
- limit individual artifact size to a reasonable default, such as 1 MB.

Add CLI options only if needed:

```text
--allow-absolute-paths
--allow-outside-root
--max-artifact-bytes 1048576
```

These options should be explicit and documented.

### 7.4 Validation and security

- Treat all public artifacts as untrusted text.
- Do not execute any artifact.
- Do not shell out.
- Do not import Python from artifact folders.
- Do not follow remote URLs.
- Do not include private/local absolute paths in generated provenance unless explicitly allowed.

### 7.5 Dependencies

Use Python standard library only.

Allowed modules include:

```text
argparse
json
pathlib
dataclasses
typing
collections
re
sys
hashlib
```

## 8. Files likely involved

Create:

```text
scripts/dataset/import_public_dataset.py
scripts/dataset/adapters/manifest.py
scripts/dataset/rtl_extract.py
scripts/dataset/draft_rows.py
docs/dataset/public_manifest_format.md
tests/dataset/test_public_import.py
tests/fixtures/public_manifest/manifest.jsonl
tests/fixtures/public_manifest/counter_candidate.v
tests/fixtures/public_manifest/counter_tb.v
```

Modify:

```text
scripts/dataset/adapters/__init__.py
scripts/dataset/adapters/base.py
scripts/dataset/adapters/verilog_eval.py
scripts/dataset/adapters/rtllm.py
scripts/dataset/adapters/rtlfixer.py
docs/dataset/public_dataset_sources.md
docs/dataset/dataset_guidelines.md
README.md
```

Do not modify unrelated files.

## 9. Data model

No database.

### 9.1 Manifest row

JSONL object fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | string | yes | Stable source example ID. |
| `source` | string | yes | Must map to allowed dataset source enum. |
| `license` | string | yes | Upstream license or usage label. |
| `design_family` | string | yes | Split/isolation family. |
| `task_type` | string | yes | Allowed v0.1 task type. |
| `user_goal` | string | yes | Allowed v0.1 user goal. |
| `artifacts` | object | yes | Paths to local files. |
| `provenance` | object | yes | Public source metadata. |

### 9.2 RawPublicExample

May extend existing dataclass to include:

```python
@dataclass(frozen=True)
class RawPublicExample:
    source_id: str
    root: Path
    artifacts: dict[str, str]
    source: str
    license: str
    design_family: str
    task_type: str
    user_goal: str
    provenance: dict[str, object]
    metadata: dict[str, object]
```

If modifying the dataclass breaks less code than adding a new one, modify it. Otherwise create a new dataclass and keep backward compatibility.

### 9.3 Generated dataset row

Generated rows must use the existing `dataset_v0.1` row envelope. No schema migration.

## 10. API contract

### Import Public Dataset

- Name: Import Public Dataset
- Method: CLI
- Path: `scripts/dataset/import_public_dataset.py`

Request:

```bash
python scripts/dataset/import_public_dataset.py \
  --adapter manifest \
  --input data/raw_public/example_manifest.jsonl \
  --output data/drafts/public_manifest_draft_v0.1.jsonl \
  --json
```

Response body:

```json
{
  "ok": true,
  "adapter": "manifest",
  "discovered_examples": 1,
  "imported_rows": 1,
  "rejected_examples": 0,
  "output": "data/drafts/public_manifest_draft_v0.1.jsonl",
  "rejected_output": "data/drafts/public_manifest_draft_v0.1.rejected.jsonl",
  "errors": [],
  "warnings": []
}
```

Error cases:

- input path missing,
- invalid adapter name,
- malformed manifest JSON,
- missing required manifest field,
- invalid source/task/user goal enum,
- artifact file missing,
- artifact path escapes root,
- artifact too large,
- duplicate output row ID,
- generated row fails dataset validation,
- no rows imported.

Exit codes:

- `0` when at least one row is imported and `--strict` is not violated,
- `1` when no rows are imported,
- `1` when any rejection occurs under `--strict`.

## 11. Edge cases

Handle:

- empty manifest,
- blank manifest lines,
- malformed JSON line,
- duplicate manifest IDs,
- missing artifact file,
- artifact path with `../`,
- absolute artifact path,
- large artifact file,
- binary or invalid UTF-8 file,
- unknown adapter,
- unknown source enum,
- task type mismatch,
- user goal mismatch,
- no RTL artifact but report artifact present,
- before/after row missing one side,
- testbench present without RTL,
- Windows path separators,
- multiple modules in one RTL file,
- generated row rejected by validator.

## 12. Testing plan

### Unit tests

Test:

- manifest parsing,
- path resolution safety,
- artifact size limit,
- RTL extraction helpers,
- draft row generation,
- adapter registry.

### Integration tests

Run:

```bash
python scripts/dataset/import_public_dataset.py \
  --adapter manifest \
  --input tests/fixtures/public_manifest/manifest.jsonl \
  --output <tmpdir>/public_manifest_draft_v0.1.jsonl \
  --json
```

Then validate output:

```bash
python scripts/dataset/validate_dataset.py --input <tmpdir>/public_manifest_draft_v0.1.jsonl --strict
```

Also verify split rejection:

```bash
python scripts/dataset/split_dataset.py --input <tmpdir>/public_manifest_draft_v0.1.jsonl --output-dir <tmpdir>/split
```

Expected: split fails without `--allow-unreviewed` because rows are draft.

### Manual checks

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

## 13. Definition of done

The task is done only when:

- Manifest adapter imports a valid synthetic fixture.
- Generated rows validate under `validate_dataset.py --strict`.
- Generated rows are always `review_status: draft` and `split: unsplit`.
- Splitter rejects imported draft rows unless `--allow-unreviewed` is used.
- Rejected examples are written to a sidecar JSONL with reasons.
- Path traversal and missing artifact files are tested.
- VerilogEval adapter delegates to manifest when `manifest.jsonl` exists.
- RTLLM and RTLFixer adapters either delegate to manifest or fail clearly with a documented message.
- No downloads, shell commands, or artifact execution are introduced.
- `python -m pytest tests/dataset` passes.
- Existing golden dataset validation still passes.

## 14. Codex implementation instructions

Implement this spec exactly.

Focus only on local public dataset draft ingestion.

Do not add model training, external LLM calls, downloads, EDA execution, or new schema versions.

Use standard-library Python only.

Preserve existing CLI behavior and tests.

Add the manifest adapter, import CLI, safe path handling, draft row generation, tests, and docs.

Run:

```bash
python -m pytest tests/dataset
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Also run the new importer manually against the test fixture and validate the generated output.

Commit and push when finished. Summarize changed files, commands run, test results, and tradeoffs.