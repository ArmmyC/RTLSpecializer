# Feature Spec: Local Model Candidate Runner v0.1

## 1. Goal

Add a local/offline-friendly model candidate runner that turns a release split into evaluator-ready candidate answer JSONL.

The repository now has a complete dataset pipeline:

```text
local public data -> draft review batch -> manual review -> readiness check -> finalization -> release -> rule baseline evaluation
```

The major missing piece is an actual model-output generator. Today the evaluator can score candidate JSONL, and the rule baseline can generate conservative candidates, but there is no tool to run a local model against a release split and create candidate answers in the expected format.

This feature should add a controlled runner for local or user-specified OpenAI-compatible chat endpoints, primarily for local LLM servers such as llama.cpp server, vLLM, Text Generation Inference, LM Studio, or Open WebUI-compatible OpenAI endpoints when explicitly configured by the user.

The intended flow is:

```text
release/test.jsonl
  -> build prompt per row
  -> call local/user-configured model endpoint
  -> parse/repair strict JSON answer object conservatively
  -> validate candidate answer format
  -> write candidates.jsonl
  -> optionally evaluate with existing evaluator
```

The runner must be safe by default:

- no endpoint is called unless explicitly configured,
- default endpoint should be localhost only,
- no training,
- no fine-tuning,
- no external downloads,
- no EDA execution,
- no RTL execution,
- no automatic claims beyond the model output validation gates.

## 2. Non-goals

Do not build:

- model training,
- fine-tuning,
- LoRA/DoRA adapters,
- model downloading,
- benchmark leaderboard publishing,
- web UI,
- prompt optimization loops,
- automatic answer correction using another LLM,
- RTL simulation or synthesis,
- EDA execution,
- automatic acceptance of model claims as true,
- cloud API defaults.

Do not require third-party dependencies. Use Python standard library unless an existing dependency is already present and justified.

## 3. User stories

- As a dataset maintainer, I want to generate model candidate answers for a release test split so I can evaluate a local LLM.
- As a researcher, I want candidate files to include model metadata, prompt template version, generation settings, and parse status.
- As a safety-focused evaluator, I want invalid or non-JSON model answers preserved but marked as parse failures rather than silently discarded.
- As a user with a local OpenAI-compatible endpoint, I want to configure endpoint URL, model name, temperature, max tokens, timeout, and retry count.
- As a future trainer, I want reproducible candidate-generation runs with resumable output and clear run metadata.

## 4. CLI UX

Add:

```text
scripts/eval/run_model_candidates.py
```

Example:

```bash
python scripts/eval/run_model_candidates.py \
  --dataset data/releases/release_v0.1_plus_verilog_eval_001/test.jsonl \
  --output data/eval/candidates/qwen_local_verilog_eval_001.jsonl \
  --model qwen3-coder-local \
  --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --prompt-template rtl_answer_v0.1_default \
  --temperature 0.0 \
  --max-tokens 2048 \
  --timeout 120 \
  --json
```

Supported options:

```text
--dataset <path>                 release split JSONL, usually test.jsonl
--output <path>                  candidate answer JSONL
--model <name>                   model identifier sent to endpoint and stored in metadata
--endpoint <url>                 OpenAI-compatible /v1/chat/completions URL; default localhost only
--api-key-env <name>             optional env var for bearer token; default none
--prompt-template <name>         default rtl_answer_v0.1_default
--temperature <float>            default 0.0
--top-p <float>                  optional
--max-tokens <int>               default 2048
--timeout <seconds>              default 120
--retries <int>                  default 1
--limit <int>                    optional first N rows for smoke tests
--row-id <id>                    optional repeatable row filter
--resume                         skip IDs already present in output
--overwrite                      replace output if it exists
--raw-output-dir <path>          optional local-only dir for raw model text/debug payloads
--evaluate-output-dir <path>     optional run evaluator after candidate generation
--strict                         fail if any row has parse/validation errors
--dry-run                        build prompts and write metadata report, but do not call endpoint
--json                           print JSON summary
```

Exit code behavior:

- Exit `0` when the runner completes and, unless `--strict`, writes candidate rows for all attempted rows.
- Exit `1` on CLI/config errors, unsafe endpoint, dataset load errors, duplicate output IDs, endpoint failures that prevent completion, or strict-mode parse/validation failures.

## 5. Candidate JSONL output format

Each output line must follow the existing evaluator candidate format:

```json
{
  "id": "row_id",
  "answer": {"schema_version":"rtl_answer_v0.1", "...":"..."},
  "metadata": {
    "model": "qwen3-coder-local",
    "runner": "run_model_candidates.py",
    "prompt_template": "rtl_answer_v0.1_default",
    "prompt_version": "v0.1",
    "endpoint_host": "127.0.0.1",
    "temperature": 0.0,
    "max_tokens": 2048,
    "attempts": 1,
    "parse_status": "parsed_json",
    "validation_status": "candidate_valid",
    "created_by": "model_runner",
    "raw_output_path": null
  }
}
```

If the model output cannot be parsed into a valid answer object, write a conservative invalid candidate object that the evaluator can process and score low, or write an `answer` object with safe defaults and metadata showing failure. Prefer preserving the evaluator contract over dropping rows.

Required metadata statuses:

```text
parse_status: parsed_json | extracted_json | parse_failed | endpoint_failed | dry_run
validation_status: candidate_valid | candidate_invalid | not_validated
```

## 6. Prompt template requirements

Create a reusable prompt builder, preferably:

```text
scripts/eval/model_prompting.py
```

Default template: `rtl_answer_v0.1_default`.

The prompt must instruct the model to output only one JSON object matching `rtl_answer_v0.1` answer content, not a full chat row. It should include:

- task type,
- user goal,
- extracted RTL summary,
- supplied artifacts with clear labels,
- tool checks and evidence status,
- strict claim-level policy,
- output schema skeleton,
- instruction to use `insufficient_evidence` for area/activity/power unless relevant reports exist,
- instruction not to claim verified correctness without passing simulation/equivalence evidence,
- instruction to include lint/compile in verification plan,
- instruction not to include Markdown fences around the JSON.

The prompt must not include hidden chain-of-thought instructions. Ask for concise reasoning fields that are directly included in the JSON answer.

## 7. Endpoint safety requirements

Default behavior should be local-only.

Requirements:

- If `--endpoint` is omitted, use `http://127.0.0.1:8000/v1/chat/completions`.
- By default, allow only localhost/loopback hosts:
  - `127.0.0.1`,
  - `localhost`,
  - `::1`.
- If a non-local endpoint is provided, fail unless an explicit flag is supplied:

```text
--allow-nonlocal-endpoint
```

- Do not log API keys.
- `--api-key-env` reads a bearer token from an environment variable only if supplied.
- The JSON summary should show endpoint host but not full credentials.
- Do not upload raw local files other than the dataset row content intentionally included in prompts.

## 8. Parsing and validation requirements

Implement parser utilities, preferably:

```text
scripts/eval/model_candidate_runner.py
```

Requirements:

- Accept raw model text.
- Parse direct JSON object.
- If needed, extract the first valid JSON object from surrounding text.
- Reject arrays and non-object JSON.
- Ensure the parsed object is an `rtl_answer_v0.1` answer object, not a full chat row.
- Validate candidate answer by reusing evaluator candidate validation or dataset validation replacement logic.
- Preserve raw output optionally under `--raw-output-dir` with safe filenames by row ID.
- Do not execute model output.

## 9. Resume and overwrite behavior

Output safety:

- If output exists and neither `--resume` nor `--overwrite` is provided, fail.
- `--overwrite` replaces the output file and any report generated by this run.
- `--resume` loads existing output, validates unique IDs, and skips completed IDs.
- If both `--resume` and `--overwrite` are provided, fail.
- Never write into `.local_data`.
- Raw output directory must not be inside `.local_data`.

## 10. Reports

Write a sidecar report next to output:

```text
<output-stem>.report.json
<output-stem>.report.md
```

Report JSON should include:

```json
{
  "ok": true,
  "dataset": "...",
  "output": "...",
  "model": "...",
  "endpoint_host": "127.0.0.1",
  "prompt_template": "rtl_answer_v0.1_default",
  "attempted_rows": 10,
  "written_rows": 10,
  "skipped_rows": 0,
  "parse_status_counts": {},
  "validation_status_counts": {},
  "errors": [],
  "warnings": []
}
```

Markdown report should include:

- run settings,
- row counts,
- parse/validation status counts,
- failed rows table,
- next command to evaluate candidates.

If `--evaluate-output-dir` is provided, run the existing evaluator after writing candidates and include evaluation summary in the report.

## 11. Tests

Add tests under:

```text
tests/eval/test_model_candidate_runner.py
```

Required tests:

- prompt builder includes task/artifact/tool-check context and schema policy,
- parser accepts direct JSON object,
- parser extracts JSON from surrounding text,
- parser rejects arrays/non-object JSON,
- candidate row metadata includes model/template/settings/status,
- dry-run writes no endpoint calls and reports `dry_run`,
- unsafe nonlocal endpoint is rejected by default,
- nonlocal endpoint is allowed only with `--allow-nonlocal-endpoint`,
- output exists fails without resume/overwrite,
- resume skips existing IDs,
- duplicate output IDs fail,
- raw output directory safe filenames are used,
- strict mode fails on parse/validation failures,
- CLI JSON output is parseable,
- optional evaluation integration works with a mocked candidate set.

No test should call a real network endpoint. Use a fake client or monkeypatch `urllib.request.urlopen`.

## 12. Docs

Create:

```text
docs/eval/model_candidate_runner.md
```

Update:

```text
README.md
docs/eval/evaluation_harness.md
```

Docs must explain:

```text
release/test.jsonl -> model candidate runner -> candidate JSONL -> evaluator
```

Also document:

- local endpoint default,
- nonlocal endpoint safety flag,
- candidate output format,
- resume/overwrite behavior,
- raw output handling,
- how to run a 3-row smoke test,
- how to evaluate generated candidates,
- limitations: scores are deterministic heuristic evaluation, not proof of RTL correctness.

## 13. Security and safety

Treat dataset rows and model outputs as untrusted text.

Do not execute RTL, testbenches, model output, or shell commands from data.

Do not call external services unless the user explicitly passes `--allow-nonlocal-endpoint`.

Do not log secrets.

Do not store API keys in reports.

Do not upload private/company RTL.

## 14. Files likely involved

Create:

```text
scripts/eval/model_prompting.py
scripts/eval/model_candidate_runner.py
scripts/eval/run_model_candidates.py
tests/eval/test_model_candidate_runner.py
docs/eval/model_candidate_runner.md
```

Modify:

```text
README.md
docs/eval/evaluation_harness.md
```

Do not modify dataset schemas unless a real bug is discovered and documented separately.

## 15. Testing plan

Run:

```bash
python -m pytest tests/eval/test_model_candidate_runner.py
python -m pytest tests/dataset tests/eval
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

Run dry-run smoke:

```bash
python scripts/eval/run_model_candidates.py \
  --dataset data/golden/golden_v0.1.jsonl \
  --output /tmp/rtl_specializer_model_candidates_dry_run.jsonl \
  --model dry-run-model \
  --limit 3 \
  --dry-run \
  --json \
  --overwrite
```

If the user has a local OpenAI-compatible endpoint running, optionally run:

```bash
python scripts/eval/run_model_candidates.py \
  --dataset data/releases/release_v0.1_plus_verilog_eval_001/test.jsonl \
  --output data/eval/candidates/local_model_verilog_eval_001.jsonl \
  --model <local-model-name> \
  --endpoint http://127.0.0.1:8000/v1/chat/completions \
  --temperature 0.0 \
  --max-tokens 2048 \
  --timeout 120 \
  --json
```

Do not fail CI if no local endpoint is running.

## 16. Definition of done

Done only when:

- CLI exists and can generate candidate JSONL from a dataset split.
- Prompt builder is reusable and tested.
- JSON parser handles direct/extracted/failure cases.
- Endpoint calls are explicit and localhost-only by default.
- Resume/overwrite behavior is safe.
- Candidate rows follow evaluator format.
- Reports are written.
- Dry-run works without network.
- Tests cover parser, prompt, endpoint safety, output safety, resume, strict mode, CLI JSON, and mocked endpoint success/failure.
- Docs explain how to run and evaluate local model candidates.
- No training, downloads, EDA execution, RTL execution, schema changes, or secret logging are introduced.

## 17. Codex implementation instructions

Implement this spec exactly.

This is an evaluator candidate-generation feature for local/user-configured models. It must not train, fine-tune, download models, run EDA tools, or execute RTL.

After finishing, commit and push. Summarize changed files, commands run, test results, smoke results, and tradeoffs.
