# Qwen2.5-Coder-7B LoRA pilot v0.1

## Status and scope

This record summarizes one local, controlled training-and-evaluation pilot. The
acceptance checker accepted the run, but acceptance applies only to the fixed
synthetic held-out split and deterministic checks described below. It is not a
model release, production-readiness decision, or claim of general RTL
correctness.

## Model and adapter identity

- Base model: `Qwen/Qwen2.5-Coder-7B-Instruct`.
- Adapter type: PEFT LoRA for causal language modeling.
- Adapter output: `outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora/`.
- Served adapter alias: `qwen2_5_coder_7b_lora_pilot`.
- Served base alias: `qwen2_5_coder_7b_base`.
- LoRA rank/alpha/dropout: `16` / `32` / `0.05`.
- Target modules: `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`,
  `up_proj`, and `down_proj`.
- Trainable parameters: 40,370,176 of 7,655,986,688 (`0.5273%`).

## Dataset

- Dataset: `rtlcoder_synthetic_teacher_distill_v0_1`.
- Split: 800 train, 100 validation, and 100 held-out test rows; split seed 42.
- Evaluation split:
  `data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl`.
- Status: teacher-distilled, synthetic, unreviewed, not golden, not approved,
  and not allowed for promotion.
- Evidence status: most rows are reference-only conservative text-inspection
  answers without simulation, lint, synthesis, formal, timing, toggle, area,
  or power evidence.
- Provenance/license status: must be confirmed before broader release or
  promotion.

## Training configuration and result

- Maximum sequence length: 4096.
- Learning rate: `1e-4`.
- Epochs: 1.
- Per-device train/evaluation batch size: 1 / 1.
- Gradient accumulation: 16 steps (effective batch size 16 on one worker).
- Seed: 42.
- Logging/save/evaluation intervals: 10 / 100 / 100 steps.
- Save limit: 2; no forced maximum-step override (`max_steps=-1`).
- Hardware gate/result: one NVIDIA L40, BF16 supported.
- Completed optimizer steps: 50.
- Training loss: `0.7814466094970703`.
- Validation loss: `0.4094552993774414`.
- Validation mean token accuracy: `0.902052053809166`.
- Training runtime: 1,324.0106 seconds.

## Serving and generation configuration

- Runtime: staged Python 3.12 environment with vLLM `0.23.0`.
- CPE endpoint: loopback-only `127.0.0.1:8011`.
- Hardware: one NVIDIA L40; Slurm partition `gpul40`.
- Dtype: BF16; maximum model length: 16,384; GPU memory utilization: 0.90.
- LoRA: enabled, maximum LoRA rank 16, exact alias required through
  `/v1/models` before generation.
- Attention backend: `FLASH_ATTN`.
- FlashInfer sampler and all-reduce paths: disabled.
- Candidate generation: temperature 0, maximum 2,048 tokens, 120-second
  request timeout, one retry, schema reminder enabled, and JSON response mode.
- Candidate validation required exact coverage of the 100 stable held-out IDs
  and zero parse/API errors before evaluation.

## CPE evaluation execution

- Slurm job: `7417` (`rtlspecializer-lora-eval`).
- Node: `gpu07.slurm.cpe.kmutt.ac.th`.
- Allocation: one GPU, eight CPUs, and 64 GiB memory.
- Started: 2026-07-10 20:58:44 UTC.
- Finished: 2026-07-10 21:33:00 UTC.
- Elapsed: 34 minutes 16 seconds.
- State/exit: `COMPLETED`, `0:0`.
- `/v1/models` exposed both `qwen2_5_coder_7b_base` and the exact adapter
  alias `qwen2_5_coder_7b_lora_pilot`.

## Acceptance results

The acceptance report recorded `accepted: true`. Every mandatory gate passed.

| Check | Result | Requirement | Passed |
|---|---:|---:|:---:|
| Candidate rows | 100 | 100 | yes |
| Matched rows | 100 | 100 | yes |
| Missing candidates | 0 | 0 | yes |
| Extra candidates | 0 | 0 | yes |
| Parse-error rows | 0 | 0 | yes |
| API-error rows | 0 | 0 | yes |
| Safety failures | 0 | 0 | yes |
| Mean score | 1.0 | at least 0.995 | yes |
| Exact duplicate groups | 0 | 0 | yes |
| Mutation-type mentions | 99 | at least 98 | yes |
| Mutated-signal mentions | 100 | 100 | yes |

All 14 reported design-family means were 1.0. The evaluator reported a minimum,
median, mean, and maximum score of 1.0 across the 100 matched rows.

## Base-versus-LoRA deltas

The exact base reference was `qwen2_5_coder_7b_base_schema` on the same held-out
split.

| Measure | Base | LoRA | Delta |
|---|---:|---:|---:|
| Mean evaluator score | 0.9964 | 1.0 | +0.0036 |
| Near-duplicate pairs | 5 | 1 | -4 |
| Exact duplicate groups | 0 | 0 | 0 |

Near-duplicate behavior is diagnostic only and was not an automatic acceptance
failure.

## Known residual findings

- The one response that did not explicitly mention the mutation type was
  `teacher_distill_rtlcoder_resyn27k_001827_reference_synthetic_wrong_reset_polarity`.
  It did identify the inverted reset behavior and relevant signal, but the
  fixed mention detector did not count an explicit mutation-type mention.
- One LoRA near-duplicate pair remains, with similarity `0.963901`:
  - `teacher_distill_rtlcoder_resyn27k_000368_reference_synthetic_wrong_reset_polarity`
  - `teacher_distill_rtlcoder_resyn27k_001817_reference_synthetic_wrong_reset_polarity`

## Relevant repository commits

- `703bf24b` — teacher-distill pilot dataset validation.
- `499e94bf` — canonical fine-tuning export.
- `21300630` — reusable training helpers, environment checks, and LoRA trainer.
- `149b33a3` and `6c4a8e2a` — reviewed CPE execution updates/blocker fixes.
- `d49020e5` — verified non-FlashInfer startup-only serving design.
- `2ec9c8ff` — accepted pilot evaluation artifacts (subsequently cleaned so
  generated runtime logs remain local rather than tracked).

## Local artifact paths

Primary adapter/training artifacts:

- `outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora/`
- `outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora/train_results.json`
- `outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora/eval_results.json`

Evaluation artifacts:

- `data/eval/candidates/qwen2_5_coder_7b_lora_pilot_schema_candidates.jsonl`
- `data/eval/raw_outputs/qwen2_5_coder_7b_lora_pilot_schema/`
- `data/eval/runs/qwen2_5_coder_7b_lora_pilot_schema/`
- `data/reports/eval/qwen2_5_coder_7b_lora_pilot_comparison.json`
- `data/reports/eval/qwen2_5_coder_7b_lora_pilot_comparison.md`
- `data/reports/eval/qwen2_5_coder_7b_lora_pilot_vs_base_diff.json`
- `data/reports/eval/qwen2_5_coder_7b_lora_pilot_vs_base_diff.md`
- `data/reports/eval/qwen2_5_coder_7b_lora_pilot_acceptance.json`
- `data/reports/eval/qwen2_5_coder_7b_lora_pilot_acceptance.md`

Runtime evidence remains local and ignored under `logs/` and `models.json`.
The cleanup commit removes these files only from Git tracking; it does not
delete the local experiment copies.

## Artifact hashes

Hashes were generated from the CPE-local artifacts before this record was
written.

| Artifact | SHA-256 |
|---|---|
| `data/eval/candidates/qwen2_5_coder_7b_lora_pilot_schema_candidates.jsonl` | `3d350e65f7e54d75fc2b492868a9b3c4f4d32d9876a2e8f6670e685ac9cb1e14` |
| `data/eval/runs/qwen2_5_coder_7b_lora_pilot_schema/metrics.json` | `798baf33c1c3bef0a5a98383ad7276104d902d3cad3e6ec5413f3045b0b85bab` |
| `data/reports/eval/qwen2_5_coder_7b_lora_pilot_acceptance.json` | `95c08476e95d8167749f2b9bc2318351059ed0b0db2956a0156bcbd805a6a7b5` |
| `data/reports/eval/qwen2_5_coder_7b_lora_pilot_vs_base_diff.json` | `ae7497a587381b3a4bc5b332a9d2818965264c74b46e3daf4dfeac6a57684541` |

## Limitations and prohibited claims

- Do not describe this dataset as human-reviewed, golden, approved, or
  production truth.
- Do not interpret the evaluator score as proof of simulation, synthesis,
  formal equivalence, timing, area, power, hardware safety, or silicon
  correctness.
- Do not claim generalization beyond this 100-row synthetic split or improvement
  on real-world RTL based on this pilot.
- Do not treat the 0.0036 score delta as statistically established model
  superiority; this was one deterministic run on one related synthetic split.
- Do not claim the remaining near-duplicate pair has been resolved.
- Do not publish or promote the dataset, adapter, candidates, raw generations,
  or weights without separate human review and confirmed provenance/license.
- Do not present this pilot as production serving qualification or authorization
  to merge, release, or deploy model weights.
