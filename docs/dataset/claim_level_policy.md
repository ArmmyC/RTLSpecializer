# Claim-level policy

Claim levels are recorded independently for correctness, area, activity, and power.

- `suggestion_only`: reasoned recommendation or hypothesis without decisive tool evidence.
- `tool_supported`: relevant tool output supports the statement, but it is not a complete proof.
- `verified`: the domain has explicit, appropriate verification evidence. Correctness requires simulation or equivalence evidence.
- `insufficient_evidence`: supplied information cannot support a claim.
- `not_applicable`: the answer makes no claim in that domain.

Never infer power from toggle activity or area. Power improvement requires a real power report; area requires synthesis evidence; activity requires toggle/VCD evidence; verified correctness requires simulation or equivalence. Prefer qualified language such as “may” and state the missing check. The legacy scalar `claim_level` is migration-only and produces a warning.
