$BaseUrl = "http://100.114.11.91:8000/v1"
$Model = "active-model"
$Dataset = "data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl"
$RunName = "qwen2_5_coder_7b_base_schema"
$CandidateOut = "data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl"
$RawOutputDir = "data/eval/raw_outputs/qwen2_5_coder_7b_base_schema"
$EvalRunDir = "data/eval/runs/qwen2_5_coder_7b_base_schema"
$ComparisonMd = "data/reports/eval/qwen2_5_coder_7b_base_schema_comparison.md"
$ComparisonJson = "data/reports/eval/qwen2_5_coder_7b_base_schema_comparison.json"
$RuleDiffMd = "data/reports/eval/qwen2_5_coder_7b_base_schema_vs_rule_diff.md"
$RuleDiffJson = "data/reports/eval/qwen2_5_coder_7b_base_schema_vs_rule_diff.json"
$ActiveDiffMd = "data/reports/eval/qwen2_5_coder_7b_base_schema_vs_active_diff.md"
$ActiveDiffJson = "data/reports/eval/qwen2_5_coder_7b_base_schema_vs_active_diff.json"
$SchemaReminderFile = "docs/eval/rtl_answer_schema_reminder.md"
$RuleBaselineRun = "data/eval/runs/rtlcoder_synthetic_rule_baseline"
$ActiveBaselineRun = "data/eval/runs/rtlcoder_synthetic_active_model_base_schema"
$RuleBaselineCandidates = Join-Path $RuleBaselineRun "candidates.jsonl"
$ActiveBaselineCandidates = Join-Path $ActiveBaselineRun "candidates.jsonl"

$ErrorActionPreference = "Stop"

if (-not $env:RTLSPEC_EVAL_API_KEY) {
    throw "RTLSPEC_EVAL_API_KEY is not set. Export it in your shell before running this script."
}

if (Test-Path $EvalRunDir) {
    throw "Eval run directory already exists: $EvalRunDir. Delete or rename it before rerunning."
}

if (-not (Test-Path $Dataset)) {
    throw "Dataset file not found: $Dataset"
}

if (-not (Test-Path $SchemaReminderFile)) {
    throw "Schema reminder file not found: $SchemaReminderFile"
}

if (-not (Test-Path $RuleBaselineRun)) {
    throw "Rule baseline run directory not found: $RuleBaselineRun"
}

if (-not (Test-Path $ActiveBaselineRun)) {
    throw "Hosted active-model baseline run directory not found: $ActiveBaselineRun"
}

Write-Host "Confirm /v1/models reports Qwen/Qwen2.5-Coder-7B-Instruct before continuing."
Write-Host "Example:"
Write-Host '  curl.exe http://100.114.11.91:8000/v1/models -H "Authorization: Bearer $env:RTLSPEC_EVAL_API_KEY"'

python scripts/eval/run_openai_compatible_candidates.py `
    --dataset $Dataset `
    --output $CandidateOut `
    --base-url $BaseUrl `
    --model $Model `
    --api-key-env RTLSPEC_EVAL_API_KEY `
    --temperature 0 `
    --max-tokens 2048 `
    --timeout 120 `
    --resume `
    --raw-output-dir $RawOutputDir `
    --schema-reminder-file $SchemaReminderFile `
    --response-format-json `
    --json

python scripts/eval/evaluate_answers.py `
    --dataset $Dataset `
    --candidates $CandidateOut `
    --output-dir $EvalRunDir `
    --json

python scripts/eval/compare_eval_runs.py `
    --runs $RuleBaselineRun $ActiveBaselineRun $EvalRunDir `
    --output-md $ComparisonMd `
    --output-json $ComparisonJson `
    --json

python scripts/eval/inspect_candidate_differences.py `
    --dataset $Dataset `
    --candidates-a $RuleBaselineCandidates `
    --name-a rule_baseline `
    --candidates-b $CandidateOut `
    --name-b $RunName `
    --output-md $RuleDiffMd `
    --output-json $RuleDiffJson `
    --json

if (Test-Path $ActiveBaselineCandidates) {
    python scripts/eval/inspect_candidate_differences.py `
        --dataset $Dataset `
        --candidates-a $ActiveBaselineCandidates `
        --name-a hosted_active_model_base_schema `
        --candidates-b $CandidateOut `
        --name-b $RunName `
        --output-md $ActiveDiffMd `
        --output-json $ActiveDiffJson `
        --json
} else {
    Write-Warning "Hosted active-model baseline candidates file not found; skipping active-model candidate-difference inspection."
}
