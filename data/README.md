# Dataset storage

Each nonblank line in a dataset file is one `dataset_v0.1` JSON object containing provenance, tool-check metadata, and exactly three chat messages: a system instruction, an `rtl_task_v0.1` user object, and an `rtl_answer_v0.1` assistant object.

- `golden/`: reviewed, synthetic, hand-written seed rows.
- `raw_public/`: explicitly supplied public source material; nothing is downloaded automatically.
- `drafts/`: untrusted conversion drafts that are not training-ready.
- `processed/`: validated train/validation/test outputs.
- `heldout/`: manually isolated evaluation material.

Never commit company/private RTL, proprietary tool logs, PDK or fab information, credentials, or uncertain-license material as training-ready data. See the [dataset guidelines](../docs/dataset/dataset_guidelines.md).
