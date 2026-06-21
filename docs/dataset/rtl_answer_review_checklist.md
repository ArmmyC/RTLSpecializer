# RTL answer review checklist

Use this checklist for one `rtl_answer_v0.1` answer before readiness checking.

- [ ] `schema_version` is `rtl_answer_v0.1`.
- [ ] The task type matches the dataset row.
- [ ] The answer addresses the actual prompt and user goal.
- [ ] No placeholder, stub, or “review required” phrasing remains.
- [ ] The implementation explanation is specific enough to be useful.
- [ ] The verification plan includes lint/compile and focused simulation where appropriate.
- [ ] Correctness is not marked `verified` without evidence in the row.
- [ ] Area, activity, and power claims are `insufficient_evidence` unless relevant evidence exists.
- [ ] Limitations and missing evidence are stated plainly.
- [ ] No private or proprietary text was added.
- [ ] Generated or raw local outputs are not presented as public evidence.
- [ ] The answer is ready for strict readiness checking.

Conservative claim language can be brief: “The change appears structurally consistent, but functional correctness remains `insufficient_evidence` until lint/compile and focused simulation are completed.”
