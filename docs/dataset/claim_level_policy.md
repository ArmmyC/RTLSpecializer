# Claim-level policy

Claim levels are recorded independently for correctness, area, activity, and power.

- `suggestion_only`: reasoned recommendation or hypothesis without decisive tool evidence.
- `tool_supported`: relevant, supplied tool output supports the statement, but it is not a complete proof. A tool-check placeholder alone is not evidence; the check needs a meaningful summary or matching report artifact.
- `verified`: an appropriate check has `status: "pass"`. Correctness requires a passing simulation or equivalence check; area, activity, and power require passing synthesis, toggle, and power checks respectively.
- `insufficient_evidence`: supplied information cannot support a claim.
- `not_applicable`: the answer makes no claim in that domain.

Never infer power from toggle activity or area. Power improvement requires a real power report; area requires synthesis evidence; activity requires toggle/VCD evidence; verified correctness requires simulation or equivalence. Prefer qualified language such as “may” and state the missing check. The legacy scalar `claim_level` is migration-only and produces a warning.

Tool status semantics:

- `pass` may support `verified` in the matching domain and may support `tool_supported` when meaningful evidence is supplied.
- `unknown` may support conservative explanation of a matching supplied report, but never `verified`.
- `fail` may support diagnostic explanation of a matching failed report, but never an improvement claim or `verified`.
- `not_run`, `null`, missing checks, empty summaries, and unrelated artifacts never support `tool_supported` or `verified`.
