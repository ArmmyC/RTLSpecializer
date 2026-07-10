#!/usr/bin/env bash
# Controlled, one-shot CPE LoRA serving and held-out evaluation pilot.
set -euo pipefail

ALIAS="qwen2_5_coder_7b_lora_pilot"
BASE_MODEL_ID="Qwen/Qwen2.5-Coder-7B-Instruct"
DATASET="data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl"
CANDIDATE="data/eval/candidates/qwen2_5_coder_7b_lora_pilot_schema_candidates.jsonl"
RAW_OUTPUTS="data/eval/raw_outputs/qwen2_5_coder_7b_lora_pilot_schema"
EVAL_RUN="data/eval/runs/qwen2_5_coder_7b_lora_pilot_schema"
REPORT_PREFIX="data/reports/eval/qwen2_5_coder_7b_lora_pilot"
BASE_CANDIDATE="data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl"
BASE_RUN="data/eval/runs/qwen2_5_coder_7b_base_schema"
RULE_RUN="data/eval/runs/rtlcoder_synthetic_rule_baseline"
HOSTED_RUN="data/eval/runs/rtlcoder_synthetic_active_model_base_schema"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
escape_tar_transform_regex() {
  local value="$1" char escaped="" i
  for ((i = 0; i < ${#value}; i++)); do
    char="${value:i:1}"
    case "$char" in
      '.'|'['|'*'|'^'|'$'|'\'|',')
        escaped+="\\$char"
        ;;
      *) escaped+="$char" ;;
    esac
  done
  printf '%s' "$escaped"
}
create_staging_archive() {
  local adapter_parent adapter_name adapter_pattern
  local model_parent model_name model_pattern
  local runtime_parent runtime_name runtime_pattern
  adapter_parent="$(dirname "$adapter_source")"
  adapter_name="$(basename "$adapter_source")"
  adapter_pattern="$(escape_tar_transform_regex "$adapter_name")"
  model_parent="$(dirname "$model_source")"
  model_name="$(basename "$model_source")"
  model_pattern="$(escape_tar_transform_regex "$model_name")"
  runtime_parent="$(dirname "$vllm_runtime_source")"
  runtime_name="$(basename "$vllm_runtime_source")"
  runtime_pattern="$(escape_tar_transform_regex "$runtime_name")"
  tar -C "$source_root" -cf - scripts data docs \
    -C "$adapter_parent" "$adapter_name" \
    -C "$model_parent" "$model_name" \
    -C "$runtime_parent" "$runtime_name" \
    --transform="s,^${adapter_pattern}$,adapter," \
    --transform="s,^${adapter_pattern}/,adapter/," \
    --transform="s,^${model_pattern}$,model," \
    --transform="s,^${model_pattern}/,model/," \
    --transform="s,^${runtime_pattern}$,vllm-runtime," \
    --transform="s,^${runtime_pattern}/,vllm-runtime/,"
}
usage() { cat <<'EOF'
Usage: stage_cpe_qwen2_5_coder_7b_lora_eval.sh [--run-eval] [options]
Default mode validates inputs and planned paths only; it never calls srun or an endpoint.
Options: --run-eval --job-id ID --keep-stage --source-root PATH --model-source-dir PATH
         --adapter-source-dir PATH --vllm-python PATH --port PORT
EOF
}

run_eval=0; keep_stage=0; job_id=""; port=8011
source_root="${CPE_RTLSPECIALIZER_ROOT:-$HOME/RTLSpecializer}"
model_source="${CPE_QWEN_MODEL_DIR:-$HOME/LLMModel/qwen25-coder-7b-instruct/models/Qwen__Qwen2.5-Coder-7B-Instruct}"
adapter_source=""
vllm_python="${CPE_VLLM_PYTHON:-$HOME/LLMModel/qwen25-coder-7b-instruct/llm/bin/python3}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-eval) run_eval=1;; --keep-stage) keep_stage=1;;
    --job-id|--source-root|--model-source-dir|--adapter-source-dir|--vllm-python|--port)
      [[ $# -ge 2 ]] || die "$1 requires a value"; option="$1"; value="$2"; shift 2
      case "$option" in --job-id)job_id="$value";;--source-root)source_root="$value";;--model-source-dir)model_source="$value";;--adapter-source-dir)adapter_source="$value";;--vllm-python)vllm_python="$value";;--port)port="$value";;esac
      continue;;
    -h|--help) usage; exit 0;; *) die "unknown option: $1";;
  esac; shift
done
source_root="$(cd "$source_root" && pwd -P)" || die "source root not found"
[[ -n "$adapter_source" ]] || adapter_source="$source_root/outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora"
[[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] || die "port must be numeric"

required=("$DATASET" "docs/eval/rtl_answer_schema_reminder.md" "$BASE_CANDIDATE" "$BASE_RUN/metrics.json" "$RULE_RUN/metrics.json" "$HOSTED_RUN/metrics.json" "scripts/eval/run_openai_compatible_candidates.py" "scripts/eval/evaluate_answers.py" "scripts/eval/compare_eval_runs.py" "scripts/eval/inspect_candidate_differences.py" "scripts/eval/check_qwen2_5_coder_7b_lora_acceptance.py")
for path in "${required[@]}"; do [[ -e "$source_root/$path" ]] || die "required input missing: $source_root/$path"; done
[[ -d "$model_source" ]] || die "base model directory missing: $model_source"
[[ -f "$adapter_source/adapter_model.safetensors" ]] || die "adapter missing: $adapter_source/adapter_model.safetensors"
[[ -x "$vllm_python" ]] || die "vLLM interpreter missing: $vllm_python"
vllm_runtime_source="$(cd "$(dirname "$vllm_python")/.." && pwd -P)"
[[ -x "$vllm_runtime_source/bin/python3" ]] || die "vLLM runtime interpreter missing: $vllm_runtime_source/bin/python3"
for path in "$CANDIDATE" "$RAW_OUTPUTS" "$EVAL_RUN" "${REPORT_PREFIX}_comparison.json" "${REPORT_PREFIX}_comparison.md" "${REPORT_PREFIX}_vs_base_diff.json" "${REPORT_PREFIX}_vs_base_diff.md" "${REPORT_PREFIX}_acceptance.json" "${REPORT_PREFIX}_acceptance.md"; do [[ ! -e "$source_root/$path" ]] || die "refusing existing managed output: $source_root/$path"; done

printf 'base_model=%s\nadapter=%s\nalias=%s\n' "$BASE_MODEL_ID" "$adapter_source" "$ALIAS"
printf 'settings=temperature:0,max_tokens:2048,timeout:120,response_format_json:true\n'
if [[ "$run_eval" != 1 ]]; then printf 'preflight only; pass --run-eval to allocate GPU and serve.\n'; exit 0; fi
command -v srun >/dev/null 2>&1 || die "srun is required"

stage="/tmp/rtlspecializer-lora-eval-${USER:-user}-$$"
remote=$(cat <<EOF
set -euo pipefail
stage='$stage'; keep='$keep_stage'; port='$port'; vllm_python="\$stage/vllm-runtime/bin/python3"
stop_vllm() {
  if [[ -n "\${vllm_pid:-}" ]]; then
    kill "\$vllm_pid" 2>/dev/null || true
    wait "\$vllm_pid" 2>/dev/null || true
    vllm_pid=""
  fi
}
cleanup() {
  stop_vllm
  [[ "\$keep" = 1 ]] || rm -rf -- "\$stage"
}
trap cleanup EXIT INT TERM
mkdir -p "\$stage"; tar -xf - -C "\$stage"; cd "\$stage"; mkdir -p logs
export VIRTUAL_ENV="\$stage/vllm-runtime"
export PATH="\$VIRTUAL_ENV/bin:\$PATH"
export PYTHONPATH="\$stage"
export RTLSPEC_EVAL_API_KEY=local-lora-eval
if ! "\$vllm_python" - <<'PY' \
  > logs/vllm-runtime-probe.log 2>&1
import sys
import vllm

print("python_executable:", sys.executable)
print("python_version:", sys.version)
print("vllm_version:", vllm.__version__)
PY
then
  printf 'staged vLLM runtime probe failed\n' >&2
  tail -n 200 logs/vllm-runtime-probe.log >&2 || true
  exit 1
fi
"\$vllm_python" -m vllm.entrypoints.cli.main serve "\$stage/model" \
  --host 127.0.0.1 \
  --port "\$port" \
  --served-model-name qwen2_5_coder_7b_base \
  --enable-lora \
  --max-lora-rank 16 \
  --lora-modules "$ALIAS=\$stage/adapter" \
  --dtype bfloat16 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.90 \
  --trust-remote-code \
  > logs/finetune-cpe-lora-eval-vllm.log 2>&1 &
vllm_pid=\$!
ready=0
for _ in \$(seq 1 120); do
  if curl -fsS "http://127.0.0.1:\$port/v1/models" > models.json 2>/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "\$vllm_pid" 2>/dev/null; then
    wait "\$vllm_pid" 2>/dev/null || true
    vllm_pid=""
    printf 'vLLM exited before becoming ready\n' >&2
    tail -n 200 logs/finetune-cpe-lora-eval-vllm.log >&2 || true
    exit 1
  fi
  sleep 2
done
if [[ "\$ready" != 1 ]]; then
  printf 'vLLM readiness timed out\n' >&2
  tail -n 200 logs/finetune-cpe-lora-eval-vllm.log >&2 || true
  exit 1
fi
[[ -s models.json ]] || { printf 'vLLM readiness response was empty\n' >&2; exit 1; }
python3 - '$ALIAS' models.json <<'PY'
import json, sys
alias, path = sys.argv[1:]
payload = json.load(open(path, encoding='utf-8'))
if not isinstance(payload.get('data'), list) or not any(isinstance(model, dict) and model.get('id') == alias for model in payload['data']):
    raise SystemExit(f'vLLM did not expose exact adapter alias: {alias}')
PY
python3 scripts/eval/run_openai_compatible_candidates.py --dataset '$DATASET' --output '$CANDIDATE' --base-url http://127.0.0.1:"\$port"/v1 --model '$ALIAS' --api-key-env RTLSPEC_EVAL_API_KEY --temperature 0 --max-tokens 2048 --timeout 120 --retries 1 --raw-output-dir '$RAW_OUTPUTS' --schema-reminder-file docs/eval/rtl_answer_schema_reminder.md --response-format-json --json | tee logs/finetune-cpe-lora-eval-run.log > logs/candidate-generation.json
python3 - '$DATASET' '$CANDIDATE' logs/candidate-generation.json <<'PY'
import json, sys
dataset, candidates, report = map(open, sys.argv[1:])
expected = {json.loads(line)['id'] for line in dataset if line.strip()}
rows = [json.loads(line) for line in candidates if line.strip()]
ids = [row.get('id') for row in rows]
summary = json.load(report)
if len(rows) != 100 or len(ids) != len(set(ids)) or set(ids) != expected or summary.get('parse_error_rows') or summary.get('api_error_rows'):
    raise SystemExit('candidate generation did not satisfy strict stable-ID coverage')
PY
python3 scripts/eval/evaluate_answers.py --dataset '$DATASET' --candidates '$CANDIDATE' --output-dir '$EVAL_RUN' --strict --json > logs/evaluation-command.json
python3 scripts/eval/compare_eval_runs.py --runs '$RULE_RUN' '$HOSTED_RUN' '$BASE_RUN' '$EVAL_RUN' --output-md '${REPORT_PREFIX}_comparison.md' --output-json '${REPORT_PREFIX}_comparison.json' --json > logs/comparison-command.json
python3 scripts/eval/inspect_candidate_differences.py --dataset '$DATASET' --candidates-a '$BASE_CANDIDATE' --name-a qwen2_5_coder_7b_base_schema --candidates-b '$CANDIDATE' --name-b qwen2_5_coder_7b_lora_pilot_schema --output-md '${REPORT_PREFIX}_vs_base_diff.md' --output-json '${REPORT_PREFIX}_vs_base_diff.json' --json > logs/difference-command.json
set +e
python3 scripts/eval/check_qwen2_5_coder_7b_lora_acceptance.py --lora-metrics '$EVAL_RUN/metrics.json' --base-metrics '$BASE_RUN/metrics.json' --difference-report '${REPORT_PREFIX}_vs_base_diff.json' --candidate-report logs/candidate-generation.json --output-md '${REPORT_PREFIX}_acceptance.md' --output-json '${REPORT_PREFIX}_acceptance.json' --json > logs/acceptance-command.json
acceptance_status=\$?
set -e
stop_vllm
tar -cf - '$CANDIDATE' '$RAW_OUTPUTS' '$EVAL_RUN' '${REPORT_PREFIX}_comparison.md' '${REPORT_PREFIX}_comparison.json' '${REPORT_PREFIX}_vs_base_diff.md' '${REPORT_PREFIX}_vs_base_diff.json' '${REPORT_PREFIX}_acceptance.md' '${REPORT_PREFIX}_acceptance.json' models.json logs/vllm-runtime-probe.log logs/candidate-generation.json logs/finetune-cpe-lora-eval-vllm.log logs/finetune-cpe-lora-eval-run.log logs/evaluation-command.json logs/comparison-command.json logs/difference-command.json logs/acceptance-command.json
exit "\$acceptance_status"
EOF
)
srun_args=(--job-name=rtlspecializer-lora-eval --chdir=/tmp)
if [[ -n "$job_id" ]]; then srun_args+=(--jobid="$job_id" --overlap); else srun_args+=(--partition=gpul40 --gres=gpu:1 --cpus-per-task=8 --mem=64G --time=01:00:00); fi
artifact="$(mktemp "${TMPDIR:-/tmp}/rtlspecializer-lora-eval.XXXXXX.tar")"; trap 'rm -f -- "$artifact"' EXIT
set +e
create_staging_archive | srun "${srun_args[@]}" /bin/bash -lc "$remote" > "$artifact"
remote_status=$?
set -e
[[ -s "$artifact" ]] && tar -C "$source_root" -xf "$artifact"
exit "$remote_status"
