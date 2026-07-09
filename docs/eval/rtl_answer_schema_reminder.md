You must return exactly one JSON object and no Markdown.

The JSON object must use this exact schema:

{
  "schema_version": "rtl_answer_v0.1",
  "source_id": "<copy exactly from the user task source_id>",
  "task_type": "<copy exactly from the user task task_type>",
  "issue_summary": [
    {
      "issue": "<one concise issue or conservative observation>",
      "severity": "low|medium|high",
      "evidence": {
        "signal_names": ["<only concrete RTL signal names>"],
        "code_location": {
          "module": "<module name or null>",
          "block": "<block name or null>",
          "line_range": null
        },
        "reason": "<text-inspection reason only>"
      }
    }
  ],
  "time_reasoning": {
    "clock_cycle_behavior": "<conservative timing/cycle reasoning>",
    "latency_or_state_risk": "<risk or insufficient evidence>",
    "reset_behavior_risk": "<risk or insufficient evidence>"
  },
  "space_reasoning": {
    "hardware_resources_involved": ["<signals/resources>"],
    "area_risk": "insufficient evidence without synthesis report",
    "activity_risk": "insufficient evidence without toggle/VCD report"
  },
  "safe_optimization": {
    "recommendation": "<safe conservative recommendation>",
    "patch_style": "explanation_only",
    "expected_effect": "No correctness, area, activity, or power improvement is claimed.",
    "requires_spec_confirmation": true
  },
  "functional_risk": ["<risk>"],
  "verification_plan": [
    "Run lint/compile.",
    "Run simulation.",
    "Run synthesis before area claims.",
    "Run toggle/VCD analysis before activity claims.",
    "Use power reports before power claims."
  ],
  "claim_levels": {
    "correctness": "suggestion_only",
    "area": "insufficient_evidence",
    "activity": "insufficient_evidence",
    "power": "insufficient_evidence"
  },
  "evidence_used": [
    "artifacts.before_rtl_code",
    "artifacts.rtl_code",
    "mutation_summary",
    "mutated_signal_names",
    "prompt",
    "tool_checks"
  ],
  "limitations": [
    "tool_checks are null or absent, so parse, lint, simulation, equivalence, synthesis, power, and toggle checks were not run."
  ],
  "patch": {
    "provided": false,
    "patch_type": "none",
    "diff": null,
    "notes": "No patch is provided in this candidate answer."
  }
}

Rules:
- Do not use claim levels like high, medium, low, passed, verified, correct, or tool_supported.
- Do not claim simulation, lint, synthesis, formal, timing, area, activity, or power results unless supplied in the task.
- Do not say synthesized, verified, passed, equivalent, timing met, area improved, power improved, or activity reduced.
- If unsure, stay conservative.
