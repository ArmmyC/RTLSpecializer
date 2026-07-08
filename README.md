# RTLSpecializer

Dataset-first tooling for structured, evidence-aware RTL specialist training data.

Validate the seed dataset from the repository root:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

See [data/README.md](data/README.md) and [docs/dataset/dataset_guidelines.md](docs/dataset/dataset_guidelines.md).

Local public dataset drafts can be imported, reviewed, and promoted without downloads, EDA execution, or external LLM calls. See [docs/dataset/public_manifest_format.md](docs/dataset/public_manifest_format.md) and [docs/dataset/review_promotion_workflow.md](docs/dataset/review_promotion_workflow.md).

The local RTLCoder `Resyn27k.json` file can also be inspected into a review-only raw index and Markdown/JSON import reports without promotion or correctness assumptions. See [docs/dataset/public_dataset_sources.md](docs/dataset/public_dataset_sources.md).

Dataset releases assemble validated/reviewed rows into deterministic train/val/test artifacts with manifests, hashes, stats, and a dataset card. See [docs/dataset/release_workflow.md](docs/dataset/release_workflow.md).

Local deterministic evaluation consumes release rows plus candidate answer JSONL and writes rubric scores, metrics, and reports without model calls. See [docs/eval/evaluation_harness.md](docs/eval/evaluation_harness.md).

Evaluator-ready candidates can also be generated from a local OpenAI-compatible chat endpoint, with localhost-only defaults, strict JSON parsing, resumable output, and a network-free dry-run mode. See [docs/eval/model_candidate_runner.md](docs/eval/model_candidate_runner.md).

Run the network-free dry-run first. A localhost server and its operator can still read submitted RTL; non-local endpoints require explicit opt-in, and API key values must come from environment variables rather than configuration files. Generated candidate, raw, benchmark, and evaluation outputs remain local and should not be committed without deliberate review. Evaluator scores are heuristics, not proof of RTL correctness.

Multiple local model configurations and the rule baseline can be run through one repeatable benchmark suite with JSON, Markdown, and CSV summaries. See [docs/eval/model_benchmark_suite.md](docs/eval/model_benchmark_suite.md).

For the first reviewed VerilogEval comparison, follow the [first local benchmark runbook](docs/eval/first_local_benchmark_runbook.md) and copy a safe starting configuration from [configs/benchmarks](configs/benchmarks/README.md).

VerilogEval-derived review batches can be prepared from local staged data only; rows remain draft until human review and promotion. See [docs/dataset/verilog_eval_review_workflow.md](docs/dataset/verilog_eval_review_workflow.md).

Raw VerilogEval source tasks can also be exported into small local JSON batches for manual ChatGPT/Claude normalization into `rtl_task_v0.1`, then validated locally before later drafting and review. See [docs/dataset/llm_normalization_batch_workflow.md](docs/dataset/llm_normalization_batch_workflow.md).

Clean `rtl_task_v0.1` rows can be exported into small local teacher-answer batches for manual ChatGPT/Claude/larger-teacher generation of conservative `rtl_answer_v0.1`, then validated and merged into draft chat rows. See [docs/dataset/rtl_answer_teacher_generation_workflow.md](docs/dataset/rtl_answer_teacher_generation_workflow.md).

Clean `rtl_task_v0.1` plus clean teacher `rtl_answer_v0.1` rows can also be packaged into a teacher-distilled pilot fine-tuning dataset with deterministic train/validation/test splits, manifest hashes, and an explicit unreviewed/not-golden dataset card. See [docs/dataset/teacher_distill_finetune_pilot_workflow.md](docs/dataset/teacher_distill_finetune_pilot_workflow.md).

For the next local-only step after dataset preparation, use the teacher-distill pilot baseline-vs-fine-tuned workflow in [docs/finetune/rtl_teacher_distill_pilot.md](docs/finetune/rtl_teacher_distill_pilot.md). It exports test prompts, scores baseline and fine-tuned outputs structurally, and compares claim-safety behavior without training inside this repository task.

Human reviewers can structure a focused 60–90 minute pass with the [manual review session guide](docs/dataset/manual_review_session_guide.md) and its per-answer checklist.

Manually edited review batches can first be triaged locally for duplicated answers, placeholder task artifacts, claim wording, and reset-language risks without changing any rows. See [docs/dataset/review_triage_workflow.md](docs/dataset/review_triage_workflow.md).

Manually edited review batches can be checked locally for structural validity, changed answers, stubs, ID mismatches, and promotion-gate failures before any promotion occurs. See [docs/dataset/review_readiness_workflow.md](docs/dataset/review_readiness_workflow.md).

After every intended row passes readiness, a guarded local finalization command can connect strict promotion, deterministic release assembly, conservative baseline generation, and deterministic evaluation without replacing human review. See [docs/dataset/finalize_reviewed_batch_workflow.md](docs/dataset/finalize_reviewed_batch_workflow.md).

Codex/agent workflow guidance lives in [AGENTS.md](AGENTS.md), with reusable review and prompt templates under [docs/codex/](docs/codex/).

## CI smoke checks

GitHub Actions runs strict golden-dataset validation and the deterministic dataset/evaluation test suite on every pull request and push to `main`. CI does not call models, run EDA tools, simulate or synthesize RTL, train models, or download raw datasets. Local generated data remains ignored and must not be committed.
