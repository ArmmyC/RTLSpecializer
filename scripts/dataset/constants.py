"""Controlled vocabulary for dataset_v0.1."""

DATASET_VERSION = "dataset_v0.1"
TASK_SCHEMA_VERSION = "rtl_task_v0.1"
ANSWER_SCHEMA_VERSION = "rtl_answer_v0.1"
TASK_SCHEMA_ALIASES = {"rtl_task.v0.1"}
ANSWER_SCHEMA_ALIASES = {"rtl_answer.v0.1"}
TASK_SCHEMA_VERSIONS = {TASK_SCHEMA_VERSION, *TASK_SCHEMA_ALIASES}
ANSWER_SCHEMA_VERSIONS = {ANSWER_SCHEMA_VERSION, *ANSWER_SCHEMA_ALIASES}

TASK_TYPES = {
    "rtl_bug_review",
    "rtl_area_activity_review",
    "rtl_tool_report_explanation",
    "unsafe_optimization_rejection",
    "rtl_before_after_judgment",
}
USER_GOALS = {
    "find_correctness_bug",
    "reduce_switching_activity",
    "reduce_area",
    "explain_lint_log",
    "explain_synthesis_report",
    "explain_toggle_report",
    "compare_before_after",
    "suggest_safe_patch",
    "reject_unsafe_optimization",
}
CLAIM_LEVELS = {
    "suggestion_only", "tool_supported", "verified",
    "insufficient_evidence", "not_applicable",
}
CLAIM_DOMAINS = {"correctness", "area", "activity", "power"}
SPLITS = {"train", "val", "validation", "test", "unsplit"}
TRAINING_SPLITS = {"train", "val", "validation", "test"}
SOURCES = {
    "handwritten_golden", "synthetic_rfid_style", "public_verilog_eval",
    "public_rtllm", "public_rtllm_2", "public_rtlfixer",
    "public_openllm_rtl", "teacher_generated", "llm_converted_public",
}
REVIEW_STATUSES = {"draft", "validated", "reviewed", "rejected", "teacher_distilled_unreviewed"}
TEACHER_DISTILL_REVIEW_STATUS = "teacher_distilled_unreviewed"
TOOL_CHECKS = {"parse", "lint", "simulation", "equivalence", "synthesis", "toggle", "power"}
TOOL_STATUSES = {"pass", "fail", "not_run", "unknown"}
REQUIRED_OUTPUT = {
    "issue_summary", "time_reasoning", "space_reasoning", "safe_optimization",
    "functional_risk", "verification_plan", "claim_levels",
}
TOP_LEVEL_FIELDS = {
    "id", "dataset_version", "split", "source", "license", "design_family",
    "task_family", "created_by", "review_status", "provenance", "tool_checks", "messages",
}
PROVENANCE_FIELDS = {
    "origin", "public_dataset_name", "public_dataset_url", "source_commit", "notes",
}
ARTIFACT_FIELDS = {
    "rtl_code", "before_rtl_code", "after_rtl_code", "testbench",
    "synthesis_report", "toggle_report", "lint_log",
}
