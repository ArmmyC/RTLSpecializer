# VerilogEval LLM normalization batch workflow

This workflow prepares local raw VerilogEval tasks for manual normalization into `rtl_task_v0.1`. The repo never calls ChatGPT, Claude, external APIs, model endpoints, or downloads during this flow.

## Step 1: Stage local VerilogEval source data

Place a local VerilogEval checkout, conservative JSONL export, or manifest JSONL under a local path. Raw source data should stay local-only, for example:

```text
data/.local_data/verilog-eval-main/
```

Do not commit raw VerilogEval source files.

## Step 2: Export deterministic raw batches

```bash
python scripts/dataset/export_verilog_eval_normalization_batches.py \
  --input data/.local_data/verilog-eval-main \
  --output-dir data/review/verilog_eval_normalization_batches \
  --batch-size 10 \
  --json
```

This writes deterministic batch files such as:

```text
data/review/verilog_eval_normalization_batches/batch_001.json
data/review/verilog_eval_normalization_batches/batch_002.json
```

Each row preserves exact source text:

- `raw_prompt`
- `raw_reference_rtl`
- `raw_testbench`

The exporter does not summarize or rewrite prompt, RTL, or testbench text. It also does not create any `rtl_answer_v0.1` content.

If you intentionally want to refresh an existing batch directory, rerun with `--force`. That replaces only the managed `batch_XXX.json` files created by this tool and preserves unknown files in the same directory.

## Step 3: Manually send one batch to ChatGPT or Claude

Open:

- [llm_rtl_task_normalization_prompt.md](llm_rtl_task_normalization_prompt.md)
- one exported `batch_XXX.json`

Copy the prompt template into ChatGPT or Claude manually, then paste the raw batch JSON.

The model should return normalized `rtl_task_v0.1` task JSON only. It must not produce `rtl_answer_v0.1`, tool results, or invented logs/reports.

## Step 4: Save and validate the returned normalized batch locally

```bash
python scripts/dataset/validate_verilog_eval_normalized_batch.py \
  --raw-batch data/review/verilog_eval_normalization_batches/batch_001.json \
  --normalized returned_batch_001.json \
  --json
```

The validator checks that:

- every row keeps `source_id`,
- `schema_version` is `rtl_task_v0.1`,
- every row has top-level `tool_checks` with null values for unavailable evidence,
- `design_context.source_rtl_role` is `reference_rtl`,
- `design_context` is populated with prompt/RTL interface context (`target_module_name`, `rtl_module_name`, and `interface_ports_from_prompt`),
- visible prompt/RTL interface direction mismatches are captured in `design_context.interface_warnings` instead of rewriting source text,
- prompt-embedded buggy `TopModule` candidates are extracted exactly into `artifacts.before_rtl_code` with `design_context.prompt_embedded_candidate_rtl: true`,
- prompt-embedded context/helper RTL is marked with `design_context.prompt_embedded_context_rtl: true` and is not treated as candidate DUT source,
- `extracted_rtl_summary` is populated, including reset signals such as `resetn`, `areset`, `ar`, or `r` when the RTL uses them as resets,
- prompt/spec text stays exact,
- RTL/testbench text stays exact or null when absent,
- no assistant-answer sections appear,
- no tool evidence is invented.

Fix the prompt or returned JSON and rerun validation until it passes.

## Step 5: Continue with later local drafting and review

After a normalized task batch validates locally, it can feed later local workflows:

```text
validated rtl_task_v0.1 normalization batch
  -> teacher model or later drafting workflow creates rtl_answer_v0.1
  -> human review / triage / readiness
  -> promotion
  -> release assembly
  -> deterministic evaluation
```

This workflow only handles the first source-to-task normalization step. It does not approve rows or make them training-ready.
