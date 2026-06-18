# Public dataset sources

v0.1 provides local adapter skeletons for VerilogEval, RTLLM/RTLLM-2, and RTLFixer. They define discovery and draft-conversion interfaces but intentionally do not download or execute anything.

Before conversion, record the dataset name, canonical URL, source commit, per-example provenance, and license. If the license is uncertain, retain `review_status: draft` and exclude the row from normal split output. Public availability is not equivalent to training permission or correctness: inspect licenses, duplicates, prompts, expected behavior, and artifacts. Never combine public examples with company/private RTL or proprietary logs.
