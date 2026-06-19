# Dataset storage

Each nonblank line in a dataset file is one `dataset_v0.1` JSON object containing provenance, tool-check metadata, and exactly three chat messages: a system instruction, an `rtl_task_v0.1` user object, and an `rtl_answer_v0.1` assistant object.

- `golden/`: reviewed, synthetic, hand-written seed rows.
- `raw_public/`: explicitly supplied public source material; nothing is downloaded automatically.
- `.local_data/`: local-only raw data; never commit its contents.
- `review/`: local-only human-review workspaces by default.
- `drafts/`: untrusted generated drafts that are not training-ready and stay local by default.
- `processed/`: promoted/validated train, validation, and test rows. Consider committing them only after human review and license/provenance approval.
- `heldout/`: manually isolated evaluation material.
- `releases/`: generated release artifacts that normally stay local unless a specific release is intentionally published.
- `eval/runs/`: generated evaluation artifacts that normally stay local unless a specific result is intentionally published.

Never commit company/private RTL, proprietary tool logs, PDK or fab information, credentials, or uncertain-license material as training-ready data. See the [dataset guidelines](../docs/dataset/dataset_guidelines.md).
