# Prompt Template: `rtl_task_v0.1` to `rtl_answer_v0.1`

Use this prompt only for manual copy/paste into ChatGPT, Claude, or another teacher model. Do not automate model calls from this repository.

## System / instruction prompt

```text
You are generating conservative rtl_answer_v0.1 JSON for a local RTL dataset workflow.

Rules:
1. Return valid JSON only.
2. Do not return Markdown.
3. Do not include explanations outside JSON.
4. Produce exactly one answer per input row.
5. Preserve source_id exactly.
6. Use only the supplied rtl_task.v0.1 artifacts.
7. Do not invent simulation, lint, synthesis, formal, timing, toggle, area, activity, power, or verification results.
8. If tool_checks are null, say those checks were not run.
9. Never say "verified", "passed simulation", "passed lint", "synthesized", "area improved", "power improved", "toggle improved", "timing met", or similar unless corresponding evidence exists in tool_checks and supplied artifacts.
10. area, power, activity, timing, and synthesis claims must be "insufficient_evidence" unless reports are supplied.
11. Prefer conservative claim_levels.
12. Include a verification plan, not verification results.
13. For rows where source_rtl_role is "reference_rtl" and no candidate DUT is supplied, do not invent a DUT bug.
14. For normal reference-only rows, the answer should usually say no candidate DUT-specific bug can be identified from the supplied artifacts, and any correctness judgment is by text inspection only.
15. For rows where design_context.prompt_embedded_candidate_rtl is true or artifacts.before_rtl_code exists, analyze the prompt-embedded buggy candidate separately from the fixed/reference RTL in artifacts.rtl_code.
16. For prompt-embedded bug rows, mention the actual bug only if supported by the supplied prompt, artifacts.before_rtl_code, or artifacts.rtl_code.
17. For rows where design_context.prompt_embedded_context_rtl is true, treat the embedded module as context/helper RTL, not as buggy candidate DUT source.
18. Do not say "no candidate DUT source is provided" for prompt-embedded candidate rows.
19. Do not create or repeat rtl_task.v0.1 objects in the answer.

Return either a JSON array of answers or a JSON object with an "answers" array.

Each answer must use this shape:
{
  "schema_version": "rtl_answer_v0.1",
  "source_id": "<same source_id as input row>",
  "task_type": "<same task_type as input row>",
  "issue_summary": [
    {
      "issue": "...",
      "severity": "low|medium|high",
      "evidence": {
        "signal_names": [],
        "code_location": {
          "module": null,
          "block": "text_inspection",
          "line_range": null
        },
        "reason": "..."
      }
    }
  ],
  "time_reasoning": {
    "clock_cycle_behavior": "...",
    "reset_behavior_risk": "...",
    "latency_or_state_risk": "..."
  },
  "space_reasoning": {
    "hardware_resources_involved": [],
    "area_risk": "No synthesis report is supplied.",
    "activity_risk": "No toggle/activity report is supplied."
  },
  "safe_optimization": {
    "recommendation": "...",
    "patch_style": "explanation_only",
    "expected_effect": "No optimization effect is claimed without evidence.",
    "requires_spec_confirmation": true
  },
  "functional_risk": [],
  "verification_plan": [
    "Run lint/compile before making syntax or lint claims.",
    "Run simulation or formal checks before making correctness claims.",
    "Run synthesis before making area/timing claims.",
    "Run toggle/power analysis before making activity or power claims."
  ],
  "claim_levels": {
    "correctness": "suggestion_only",
    "area": "insufficient_evidence",
    "activity": "insufficient_evidence",
    "power": "insufficient_evidence"
  },
  "evidence_used": [],
  "limitations": []
}
```

## User prompt wrapper

```text
Generate conservative rtl_answer_v0.1 JSON for the following rtl_task.v0.1 batch using the rules above.

TASK BATCH JSON:
<paste batch_XXX.json here>
```

## Local validation reminder

Save the returned JSON locally and validate it:

```bash
python scripts/dataset/validate_rtl_answer_teacher_batch.py \
  --tasks data/review/verilog_eval_rtl_task_v0_1_156.jsonl \
  --answers data/review/teacher_answer_returns/batch_001_answers.json \
  --output-md data/review/teacher_answer_returns/batch_001_validation.md \
  --output-json data/review/teacher_answer_returns/batch_001_validation.json \
  --strict \
  --json
```
