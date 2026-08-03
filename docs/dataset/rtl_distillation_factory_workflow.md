# Verified RTL distillation factory workflow

This workflow turns a locally staged public RTL corpus into verified teacher
generation data. RTLSpecializer is the control plane: it prepares packets,
validates returned JSON, assembles private verification assets, and packages
already-ingested evidence. It does not call an LLM, download source data,
execute RTL, or run an EDA tool.

## 1. Stage and attest the source

Manually place a clean upstream checkout under an ignored local workspace. Do
not edit the checkout in place. Record the exact upstream commit and license
files, then audit the source:

```bash
python scripts/dataset/audit_rtl_generation_sources.py \
  --input data/raw/verilog_eval/upstream/dataset_spec-to-rtl \
  --source-root data/raw/verilog_eval/upstream \
  --source-commit <40-character-commit> \
  --output-json data/reports/inventory/verilog_eval_v001_audit.json \
  --output-markdown data/reports/inventory/verilog_eval_v001_audit.md \
  --inventory-output data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --acquisition-output data/reports/inventory/verilog_eval_v001_acquisition.json \
  --json
```

The inventory is metadata-only. It contains hashes, readiness, dependency
counts, interface hints, and source-relative identities; it does not contain
reference RTL, testbench text, support-file contents, or absolute paths.
The command also emits missing-file, duplicate-source-ID, and license reports
beside the inventory and acquisition manifest.

## 2. Freeze the split

Freeze the source IDs before any teacher generation:

```bash
python scripts/dataset/freeze_rtl_generation_split.py \
  --inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --output data/reports/validation/verilog_eval_v001_split.json \
  --train-ratio .70 \
  --validation-ratio .15 \
  --test-ratio .15 \
  --seed 7
```

The split is source-level, family-isolated, and must cover every inventory row
exactly once. Only `train` IDs with `executable_ready` readiness may enter
teacher generation.

## 2a. Correct reference-only testbench dependencies

The first smoke correction set is explicitly train-only and does not change
the frozen split. Select the five bounded rows:

```bash
python scripts/dataset/select_rtl_generation_asset_corrections.py \
  --inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --split-manifest data/reports/validation/verilog_eval_v001_split.json \
  --output data/reports/validation/verilog_eval_v001_asset_correction_selection.json
```

Manually author public-spec-derived testbenches under
`data/raw/internal/verilog_eval_assetfix_v002/tasks/<source_id>/testbench.sv`.
They must instantiate `TopModule` directly and must not depend on
`RefModule`, reference RTL, includes, packages, interfaces, support files, or
private paths. Create and validate the hash-bound correction manifest:

```bash
python scripts/dataset/create_rtl_generation_asset_correction_manifest.py \
  --inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --selection data/reports/validation/verilog_eval_v001_asset_correction_selection.json \
  --correction-root data/raw/internal/verilog_eval_assetfix_v002 \
  --source-commit <40-character-commit> \
  --output data/raw/internal/verilog_eval_assetfix_v002/manifest.jsonl

python scripts/dataset/validate_rtl_generation_asset_corrections.py \
  --input data/raw/verilog_eval/upstream/dataset_spec-to-rtl \
  --source-root data/raw/verilog_eval/upstream \
  --source-commit <40-character-commit> \
  --base-inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --split-manifest data/reports/validation/verilog_eval_v001_split.json \
  --selection data/reports/validation/verilog_eval_v001_asset_correction_selection.json \
  --correction-root data/raw/internal/verilog_eval_assetfix_v002 \
  --correction-manifest data/raw/internal/verilog_eval_assetfix_v002/manifest.jsonl \
  --corrected-inventory data/reports/inventory/verilog_eval_assetfix_v002_rows.jsonl \
  --corrected-acquisition data/reports/inventory/verilog_eval_assetfix_v002_acquisition.json \
  --corrected-audit data/reports/inventory/verilog_eval_assetfix_v002_audit.json \
  --corrected-missing data/reports/inventory/verilog_eval_assetfix_v002_missing.json \
  --corrected-duplicates data/reports/inventory/verilog_eval_assetfix_v002_duplicates.json \
  --corrected-license data/reports/inventory/verilog_eval_assetfix_v002_license.json \
  --report data/reports/validation/verilog_eval_assetfix_v002_manifest.json
```

The corrected inventory must contain 156 rows, five `executable_ready` rows,
151 remaining `needs_testbench` rows, and zero duplicate IDs. Negative
mutation cases are recorded as offline public-spec contracts; execute them
through isolated RTLBench in a separate task before opening the LLM
normalization gate.

Select the five-task smoke allowlist before exporting packets:

```bash
python scripts/dataset/select_rtl_generation_smoke.py \
  --inventory data/reports/inventory/verilog_eval_assetfix_v002_rows.jsonl \
  --base-inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --correction-manifest data/raw/internal/verilog_eval_assetfix_v002/manifest.jsonl \
  --split-manifest data/reports/validation/verilog_eval_v001_split.json \
  --ids-output data/reports/validation/verilog_eval_v001_smoke_ids.txt \
  --report-output data/reports/validation/verilog_eval_v001_smoke_selection.json \
  --json
```

## 3. Manually normalize public tasks with an LLM

Export a small public packet. The split manifest is a gate, not a prompt
instruction:

```bash
python scripts/dataset/export_rtl_generation_normalization_batches.py \
  --input data/raw/verilog_eval/upstream/dataset_spec-to-rtl \
  --source-root data/raw/verilog_eval/upstream \
  --source-commit <40-character-commit> \
  --inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --split-manifest data/reports/validation/verilog_eval_v001_split.json \
  --split train \
  --source-ids-file data/reports/validation/verilog_eval_v001_smoke_ids.txt \
  --correction-manifest data/raw/internal/verilog_eval_assetfix_v002/manifest.jsonl \
  --correction-root data/raw/internal/verilog_eval_assetfix_v002 \
  --output-dir data/review/rtl_generation_normalization_batches \
  --private-output-dir data/.local_data/rtl_generation_verification_assets \
  --batch-size 5 \
  --json
```

Submit only the public batch and the normalization prompt to the chosen LLM
manually. Save the returned JSON under the ignored review workspace. Validate
the return with `validate_rtl_generation_normalized_batch.py`. A failed
validation stays quarantined; there is no deterministic semantic fallback.

For the v002 smoke run, use the pinned five-row wrapper. It checks the source
tree, frozen split, correction report, exact source-ID order, and public leak
boundary before calling the generic exporter:

```bash
python scripts/dataset/prepare_rtl_generation_normalization_run.py \
  --input data/raw/verilog_eval/upstream/dataset_spec-to-rtl \
  --source-root data/raw/verilog_eval/upstream \
  --inventory data/reports/inventory/verilog_eval_assetfix_v002_rows.jsonl \
  --base-inventory data/reports/inventory/verilog_eval_v001_rows.jsonl \
  --split-manifest data/reports/validation/verilog_eval_v001_split.json \
  --source-ids-file <pilot_003 ordered five-ID file> \
  --correction-manifest data/raw/internal/verilog_eval_assetfix_v002/manifest.jsonl \
  --correction-report data/reports/validation/verilog_eval_assetfix_v002_manifest.json \
  --output-dir <pilot_003>/normalization/packets \
  --private-output-dir <pilot_003>/private_assets \
  --attestation-output <pilot_003>/reports/normalization_attestation.json
```

Submit only the public batch and normalization prompt to the chosen LLM
manually. Validate the untouched return with
`validate_rtl_generation_smoke_normalization.py`; the response must be an
object containing only `rows`, with the same five rows and order. Preserve a
failed response as a new quarantine attempt rather than editing it.

After normalization validates, assemble with
`assemble_rtl_generation_smoke_inputs.py`. The assembly attestation binds the
corrected testbench hashes and original reference hashes without exposing
either file to the teacher.

## 3a. Qualify corrected verification assets before teacher generation

Independently author one public-spec passing RTL and the recorded negative
mutations for each corrected task. Do not use upstream reference RTL. Run the
RTLBench mutation manifest only through the approved isolated `pilot-docker`
workflow. Then validate the resulting evidence and runner sidecar:

```bash
python scripts/dataset/validate_rtl_generation_asset_qualification.py \
  --tasks <pilot_003>/tasks/generation_tasks.jsonl \
  --assets <pilot_003>/tasks/verification_assets.jsonl \
  --correction-manifest data/raw/internal/verilog_eval_assetfix_v002/manifest.jsonl \
  --correction-report data/reports/validation/verilog_eval_assetfix_v002_manifest.json \
  --qualification-manifest <qualification>/mutation_manifest.jsonl \
  --qualification-evidence <qualification>/mutation_evidence.jsonl \
  --qualification-sidecar <qualification>/mutation_evidence.runner.json \
  --workspace-root <qualification>/workspace \
  --report-output <pilot_003>/reports/asset_qualification.json
```

The validator requires every mutation contract to be covered, all original
and repaired candidates to pass, every mutation to be detected, no timeout or
unexpected diagnostic, exact input hashes, no reference collision, and a
rootful pilot-docker runner identity. A failed qualification stops the flow;
it never produces an RTL-candidate repair packet.

Before teacher generation, bind the passed retry with
`bind_rtl_generation_qualification_retry.py`. The binding is append-only with
respect to the failed qualification report and creates a derived private asset
view containing the corrected testbench bytes. Generation handoffs and the
final package must carry the binding hash and per-task corrected-testbench
hash. The failed qualification report remains preserved but is never accepted
as the generation qualification.

## 4. Generate and verify candidates manually

Use the existing manual teacher-generation workflow. For each training task:

```text
public task packet
→ manual LLM candidate response
→ strict candidate validation
→ private attempt-1 handoff
→ one isolated RTLBench invocation
→ sanitized evidence ingestion
```

Candidate retries are bounded to four attempts. Repair packets are created only
after a candidate-caused compile failure, functional mismatch, or timeout.
Asset, normalization, runner, and infrastructure failures return to their
owning workflow and do not produce RTL repair prompts. Stop after the first accepted candidate; do not
generate extra diversity candidates in the default path. Keep all evidence and
attempt-history records immutable.

For a multi-task response exchange, validate every packet/response pair before
publishing `candidate_records.jsonl`:

```bash
python scripts/dataset/validate_rtl_teacher_generation_response_set.py \
  --packets <pilot_003>/teacher/packets \
  --responses <pilot_003>/teacher/responses \
  --private-assets <teacher-private-assets>/verification_assets.jsonl \
  --private-assets-root <teacher-private-assets> \
  --output <pilot_003>/teacher/candidate_records.jsonl \
  --json
```

The command stages each record and writes the canonical output only when all
responses pass strict validation; it never invokes a model or executes RTL.

## 5. Package verified generation rows

Package only after evidence and attempt history have been validated:

```bash
python scripts/dataset/package_verified_rtl_generation_dataset.py \
  --tasks <generation_tasks.jsonl> \
  --candidates <candidate_records.jsonl> \
  --attempts <generation_attempts.jsonl> \
  --split-manifest data/reports/validation/verilog_eval_v001_split.json \
  --evidence <candidate_evidence.jsonl> \
  --sidecar <candidate_evidence.jsonl.runner.json> \
  --expected-rtlbench-commit <RTLBench-commit> \
  --asset-qualification <pilot_003>/reports/asset_qualification.json \
  --qualification-binding <pilot_003>/reports/asset_qualification_retry_01/teacher_generation_binding.json \
  --require-asset-qualification \
  --output-dir data/distill/verilog_eval_verified_generation_v0_1 \
  --strict \
  --json
```

The primary `train.jsonl` uses a generation-specific SFT row with a standard
system/user/assistant message envelope. The assistant content is the verified
RTL string, not an `rtl_answer_v0.1` review object. The package also emits
`all.jsonl`, `rejected_rows.jsonl`, `manifest.json`, `statistics.json`,
`dataset_card.md`, `validation_report.json`, and `provenance_report.json`.

Rows remain `automated_verified_unreviewed`, `not_approved`, and
`promotion_allowed: false`. The package excludes private reference RTL,
testbenches, support files, raw logs, and private paths.

Recovery packages are append-only. Preserve every failed partial package and
record its tree hash outside the package. Before authorizing a recovery
publication, run the focused privacy regressions, the final full test suite,
and the exact packager path in a noncanonical directory outside
`data/distill/`. Publish the recovery package once into a new path; it remains
`pending_finalization` and non-consumable until complete-run validation, output
freezing, and a conservative public-row review pass. The finalization tool
validates a staged consumable view and atomically updates only the managed
package metadata. Never use `--force` to repair a failed package.

Validate the emitted package against the same qualification report:

```bash
python scripts/dataset/validate_verified_rtl_generation_dataset.py \
  --input-dir data/distill/verilog_eval_verified_generation_v0_1 \
  --asset-qualification <pilot_003>/reports/asset_qualification.json \
  --qualification-binding <pilot_003>/reports/asset_qualification_retry_01/teacher_generation_binding.json \
  --require-asset-qualification
```

## 6. Reproducibility and scale gates

Keep a private ignored exchange manifest containing model/provider labels,
prompt-template versions, packet/response hashes, validation results, and
timestamps. Do not place that data in teacher-visible packets or training
messages.

The first rollout is a five-task smoke batch. Scale only after it yields
accepted verified rows with no infrastructure failure, privacy violation,
stale hash, or packaging error. Expand in controlled stages before processing
the complete executable-ready training pool. Validation and test IDs remain
untouched by teacher generation.
