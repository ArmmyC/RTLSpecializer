# Feature Spec: RTL Generation Task Preparation v0.1

## 1. Goal

Prepare existing local public RTL data for a later teacher-generation and private
verification workflow:

```text
local source data
  -> deterministic discovery and private-asset extraction
  -> teacher-visible normalization batch
  -> manual LLM normalization into rtl_generation_task_v0.1
  -> deterministic preservation and leakage validation
  -> private verification-asset assembly
```

The implementation is local and deterministic. It never calls a model or an
external API, executes RTL or testbenches, invokes EDA tools, or trains a model.

## 2. Audit-driven scope

The local audit found:

- a VerilogEval checkout with 156 `dataset_spec-to-rtl` prompt/reference/test
  triplets;
- a parallel 156-row `dataset_code-complete-iccad2023` view with interface files;
- existing VerilogEval raw normalization batches and normalized
  `rtl_task_v0.1` JSONL rows;
- RTLCoder raw and normalized rows without testbenches; and
- existing teacher-answer and teacher-distill artifacts that are outputs of
  other workflows, not canonical generation-task inputs.

The canonical v0.1 input is a local VerilogEval checkout containing
`dataset_spec-to-rtl/*_prompt.txt`, `*_ref.sv`, and `*_test.sv`. A normalized
VerilogEval `rtl_task_v0.1` JSONL file is supported as a fallback. The exporter
uses the local checkout's MIT `LICENSE` metadata when the adapter's generic
placeholder license is otherwise the only available value. Existing normalized
rows preserve their source license/provenance and therefore remain blocked when
their license is an unresolved placeholder.

The audit reports and raw/local dataset contents are ignored local artifacts and
are not committed.

## 3. Non-goals and compatibility

This feature does not implement teacher RTL generation, model calls, RTLBench,
candidate repair, fine-tuning, simulation, synthesis, lint execution, or any
other EDA or shell execution derived from data. It does not automatically review,
promote, approve, or mark rows training-ready.

Existing `rtl_task_v0.1`, `rtl_answer_v0.1`, and `dataset_v0.1` schemas and their
review semantics remain unchanged. Reference RTL and testbenches are private
verification assets. They are never included in teacher-visible generation
tasks, which contain no expected vectors, benchmark answers, reports, logs, or
tool results.

## 4. Inputs and outputs

The reusable module supports:

1. the audited VerilogEval checkout layout; and
2. normalized VerilogEval `rtl_task_v0.1` JSON or JSONL rows containing
   `prompt`, `source_id`, provenance/license metadata, `artifacts.rtl_code`,
   and `artifacts.testbench`.

RTLCoder rows without a deterministic testbench are accepted only for audit
metadata or export as non-ready rows; they are never treated as executable-ready
by this feature.

The public normalization batch contains only a logical `source_label`, source
IDs, provenance, exact raw specification text, deterministic hints, and
warnings. It never contains an `input` path. The private workspace contains
copied reference/testbench/support bytes and a JSONL asset manifest.

## 5. Deterministic task identity and hints

For each source row, `task_id` is the stable SHA-256-derived identifier
`rtlgen_<source-dataset-slug>_<source-id-slug>_<digest>`. The digest is the
SHA-256 of stable, sorted-key JSON containing exactly:

```json
{
  "source_dataset": "...",
  "source_id": "...",
  "source_commit": "... or null",
  "license_identity": "normalized license label"
}
```

The source dataset and source ID are exact metadata values. A source commit is
included when available; normalized license identity is the fallback identity
component. Provenance notes, local paths, and URLs are excluded. The same
canonical identity always receives the same task ID; changing source ID or
commit changes it, while changing notes or local checkout path does not.

Prompt text is preserved byte-for-byte as decoded UTF-8 text and is not prefixed,
summarized, or moved into a tool-evidence field. Top-module and interface hints
are conservative regex metadata only. They are not permission to invent missing
behavior.

## 6. Safety requirements

- All source text and files are untrusted data and are never executed.
- Source, output, private workspace, and managed files must not be symlinks.
- Relative private paths must remain below the private workspace root and must not
  contain traversal components or absolute paths.
- Private paths use normalized POSIX separators only. Reference and testbench
  paths are beneath `workspace/<task_id>/`; support paths are beneath
  `workspace/<task_id>/support/`. Manifest hashes are lowercase SHA-256 values,
  and support hash paths exactly match the support-file list.
- Public JSON is checked for full reference RTL, full testbench, support contents,
  exact/resolved/repository-relative input and output paths, `.local_data`,
  absolute/Windows/workspace paths, forbidden private field names, expected
  vectors, answer schemas, and tool-result fields before it is written. Public
  provenance URLs remain allowed.
- Files are written with stable JSON formatting, newline termination, and atomic
  replacement after all preflight checks pass.
- `--force` replaces only exact managed outputs. Unknown files remain untouched.
- Input/output aliases, hard-link collisions, and output directories below
  `.local_data` are rejected.
- No generated or private data is committed.

## 7. CLIs

The thin wrappers call `scripts/dataset/rtl_generation_preparation.py`:

```bash
python scripts/dataset/audit_rtl_generation_sources.py --help
python scripts/dataset/export_rtl_generation_normalization_batches.py \
  --input <audited-source-input> \
  --output-dir data/review/rtl_generation_normalization_batches \
  --private-output-dir data/.local_data/rtl_generation_verification_assets \
  --batch-size 5 --limit 5 --json

python scripts/dataset/validate_rtl_generation_normalized_batch.py \
  --raw-batch <public-batch.json> \
  --normalized <returned-batch.json> \
  --private-assets <verification_assets.jsonl> --json

python scripts/dataset/assemble_rtl_generation_inputs.py \
  --normalized <validated-normalized-json> \
  --private-assets <verification_assets.jsonl> \
  --tasks-output <generation_tasks.jsonl> \
  --assets-output <verification_assets.jsonl> --json
```

The exporter supports positive `--batch-size`, optional `--limit`, deterministic
`--start-index`, and safe `--force`. It refuses zero exported rows. Audit output
is local-only JSON and Markdown. Validation never rewrites its inputs. Assembly
accepts a private manifest superset: every normalized task must have exactly one
asset, duplicate or missing task IDs fail, and extra private records are omitted
from the assembled manifest and reported as `unused_private_asset_count`.
Before writing, assembly recomputes the specification, reference RTL,
testbench, and support-file byte hashes and rejects any mismatch, missing file,
symlink, or path escape.

## 8. Verification

Committed tests use only small synthetic public-safe fixtures and cover discovery,
readiness classification, exact text/hash preservation, deterministic batching,
public/private separation, leakage rejection, normalized-row validation, safe
overwrite behavior, path/symlink/alias safety, atomic output behavior, and
deterministic assembly. Run the focused five test files first, then the existing
dataset and evaluation suites.
