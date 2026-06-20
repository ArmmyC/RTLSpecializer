# RTLSpecializer

Dataset-first tooling for structured, evidence-aware RTL specialist training data.

Validate the seed dataset from the repository root:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

See [data/README.md](data/README.md) and [docs/dataset/dataset_guidelines.md](docs/dataset/dataset_guidelines.md).

Local public dataset drafts can be imported, reviewed, and promoted without downloads, EDA execution, or external LLM calls. See [docs/dataset/public_manifest_format.md](docs/dataset/public_manifest_format.md) and [docs/dataset/review_promotion_workflow.md](docs/dataset/review_promotion_workflow.md).

Dataset releases assemble validated/reviewed rows into deterministic train/val/test artifacts with manifests, hashes, stats, and a dataset card. See [docs/dataset/release_workflow.md](docs/dataset/release_workflow.md).

Local deterministic evaluation consumes release rows plus candidate answer JSONL and writes rubric scores, metrics, and reports without model calls. See [docs/eval/evaluation_harness.md](docs/eval/evaluation_harness.md).

Evaluator-ready candidates can also be generated from a local OpenAI-compatible chat endpoint, with localhost-only defaults, strict JSON parsing, resumable output, and a network-free dry-run mode. See [docs/eval/model_candidate_runner.md](docs/eval/model_candidate_runner.md).

Multiple local model configurations and the rule baseline can be run through one repeatable benchmark suite with JSON, Markdown, and CSV summaries. See [docs/eval/model_benchmark_suite.md](docs/eval/model_benchmark_suite.md).

VerilogEval-derived review batches can be prepared from local staged data only; rows remain draft until human review and promotion. See [docs/dataset/verilog_eval_review_workflow.md](docs/dataset/verilog_eval_review_workflow.md).

Manually edited review batches can be checked locally for structural validity, changed answers, stubs, ID mismatches, and promotion-gate failures before any promotion occurs. See [docs/dataset/review_readiness_workflow.md](docs/dataset/review_readiness_workflow.md).

After every intended row passes readiness, a guarded local finalization command can connect strict promotion, deterministic release assembly, conservative baseline generation, and deterministic evaluation without replacing human review. See [docs/dataset/finalize_reviewed_batch_workflow.md](docs/dataset/finalize_reviewed_batch_workflow.md).
