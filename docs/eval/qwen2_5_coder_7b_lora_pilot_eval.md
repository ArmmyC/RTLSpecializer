# Qwen2.5-Coder-7B LoRA Pilot Evaluation

This pilot serves the unchanged Qwen2.5-Coder-7B base with the completed adapter dynamically loaded as `qwen2_5_coder_7b_lora_pilot`. It uses vLLM `0.23.0` through `/storage/slurm/home/67070501002@cpe.kmutt.ac.th/LLMModel/qwen25-coder-7b-instruct/llm/bin/python3` and verified `--enable-lora`, `--max-lora-rank 16`, and `--lora-modules name=path` options.

On CPE, first run the non-executing preflight:

```bash
cd ~/RTLSpecializer
bash scripts/eval/stage_cpe_qwen2_5_coder_7b_lora_eval.sh
```

After reviewing paths and ensuring outputs are absent, run the one-shot evaluation:

```bash
bash scripts/eval/stage_cpe_qwen2_5_coder_7b_lora_eval.sh --run-eval
```

Use `--job-id ID` to reuse an allocation. Monitor `squeue -u "$USER"` and the restored logs under `logs/`. The server is loopback-only and is terminated after evaluation. Reruns refuse existing LoRA outputs; remove only the exact managed output paths after review.

The held-out structured evaluator is decisive. Training loss and token-level accuracy are not acceptance criteria. Acceptance requires 100 stable-ID candidates and matches, zero API/parse/safety failures, score at least 0.995, zero exact duplicate groups, mutation-type coverage at least 98, and mutated-signal coverage of 100.
