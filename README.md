# RTLSpecializer

Dataset-first tooling for structured, evidence-aware RTL specialist training data.

Validate the seed dataset from the repository root:

```bash
python scripts/dataset/validate_dataset.py --input data/golden/golden_v0.1.jsonl --strict
```

See [data/README.md](data/README.md) and [docs/dataset/dataset_guidelines.md](docs/dataset/dataset_guidelines.md).

Local public dataset drafts can be imported, reviewed, and promoted without downloads, EDA execution, or external LLM calls. See [docs/dataset/public_manifest_format.md](docs/dataset/public_manifest_format.md) and [docs/dataset/review_promotion_workflow.md](docs/dataset/review_promotion_workflow.md).

Dataset releases assemble validated/reviewed rows into deterministic train/val/test artifacts with manifests, hashes, stats, and a dataset card. See [docs/dataset/release_workflow.md](docs/dataset/release_workflow.md).
