# Offline LLM conversion contract

An LLM-produced row is a draft only. Conversion occurs offline; repository tooling does not call an LLM or download a dataset.

The converter must emit the complete `dataset_v0.1` envelope and three-message schema, preserve source and license provenance, and mark the row `draft`. It must not invent parser, lint, simulation, equivalence, synthesis, toggle, or power evidence. With no evidence, claims must use `suggestion_only`, `insufficient_evidence`, or `not_applicable` and conservative wording.

The validator is the authority. Human source/license and engineering review is required before a public converted row becomes `validated` or `reviewed` and therefore training-ready. Rejected output must be corrected from source evidence, not cosmetically rewritten to evade a validator rule.
