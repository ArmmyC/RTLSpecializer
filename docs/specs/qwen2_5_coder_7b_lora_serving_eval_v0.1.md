# Qwen2.5-Coder-7B LoRA Serving Evaluation v0.1

## Goal
Serve the completed Qwen2.5-Coder-7B LoRA adapter on CPE and evaluate it on the existing 100-row held-out RTLCoder synthetic test split.

## Authorized Scope
- Stage the unchanged `Qwen/Qwen2.5-Coder-7B-Instruct` base model, completed adapter, and existing vLLM runtime into GPU-local storage.
- Start loopback-only OpenAI-compatible vLLM serving with alias `qwen2_5_coder_7b_lora_pilot`.
- Generate candidates, evaluate them with the existing evaluator, compare against rule, hosted, and exact base runs, and write deterministic acceptance reports.

## Non-goals
Do not train, merge or modify weights, publish weights, provide production or multi-adapter serving, change the held-out data/prompt/evaluator, promote data, overwrite baseline runs, or commit generated candidates, raw outputs, eval runs, adapters, or model files.

## Model Identity
The endpoint must expose `qwen2_5_coder_7b_lora_pilot`; evaluation fails if it only exposes the unchanged base model. The base model remains `Qwen/Qwen2.5-Coder-7B-Instruct`.

## Evaluation Contract
Use the existing 100-row `data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl`, stable IDs, prompt builder, schema reminder, JSON response mode, `temperature=0`, `max_tokens=2048`, `timeout=120`, strict validation, and existing evaluator. Generated outputs use the isolated LoRA pilot paths and remain uncommitted.

## Acceptance
Mandatory gates: 100 candidate and matched rows; zero missing, extra, API, parse, and safety failures; mean score at least `0.995`; zero exact duplicate groups; mutation-type mentions at least 98; and mutated-signal mentions 100. Near-duplicate behavior is reported with the fixed existing algorithm, not tuned after output inspection.
