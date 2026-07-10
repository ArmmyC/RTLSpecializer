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
shell_quote() {
  printf '%q' "$1"
}
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
Options: --run-eval --startup-only --job-id ID --keep-stage --source-root PATH --model-source-dir PATH
         --adapter-source-dir PATH --vllm-python PATH --host-gcc PATH --host-gxx PATH
         --port PORT
EOF
}

run_eval=0; startup_only=0; keep_stage=0; job_id=""; port=8011; host_gcc=""; host_gxx=""
source_root="${CPE_RTLSPECIALIZER_ROOT:-$HOME/RTLSpecializer}"
model_source="${CPE_QWEN_MODEL_DIR:-$HOME/LLMModel/qwen25-coder-7b-instruct/models/Qwen__Qwen2.5-Coder-7B-Instruct}"
adapter_source=""
vllm_python="${CPE_VLLM_PYTHON:-$HOME/LLMModel/qwen25-coder-7b-instruct/llm/bin/python3}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-eval) run_eval=1;; --startup-only) startup_only=1;; --keep-stage) keep_stage=1;;
    --job-id|--source-root|--model-source-dir|--adapter-source-dir|--vllm-python|--host-gcc|--host-gxx|--port)
      [[ $# -ge 2 ]] || die "$1 requires a value"; option="$1"; value="$2"; shift 2
      case "$option" in --job-id)job_id="$value";;--source-root)source_root="$value";;--model-source-dir)model_source="$value";;--adapter-source-dir)adapter_source="$value";;--vllm-python)vllm_python="$value";;--host-gcc)host_gcc="$value";;--host-gxx)host_gxx="$value";;--port)port="$value";;esac
      continue;;
    -h|--help) usage; exit 0;; *) die "unknown option: $1";;
  esac; shift
done
if [[ -n "$host_gcc" || -n "$host_gxx" ]]; then
  [[ -n "$host_gcc" && -n "$host_gxx" ]] || die "both --host-gcc and --host-gxx must be supplied together"
fi
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

stage="/tmp/rtl-lora-eval-$$"
host_gcc_remote="$(shell_quote "$host_gcc")"
host_gxx_remote="$(shell_quote "$host_gxx")"
remote=$(cat <<EOF
set -euo pipefail
stage='$stage'; keep='$keep_stage'; port='$port'; startup_only='$startup_only'; vllm_python="\$stage/vllm-runtime/bin/python3"
requested_host_gcc=$host_gcc_remote
requested_host_gxx=$host_gxx_remote
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
select_cuda_host_compiler() {
  local gcc_candidate="" gxx_candidate="" gcc_major gxx_major
  [[ -n "\${requested_host_gcc:-}" && -n "\${requested_host_gxx:-}" ]] || {
    printf 'both --host-gcc and --host-gxx must be supplied together\n' >&2
    return 1
  }
  gcc_candidate="\$requested_host_gcc"
  gxx_candidate="\$requested_host_gxx"
  [[ -x "\$gcc_candidate" ]] || {
    printf 'supported GCC 12 compiler was not found\n' >&2
    return 1
  }
  [[ -x "\$gxx_candidate" ]] || {
    printf 'supported G++ 12 compiler was not found\n' >&2
    return 1
  }
  gcc_major="\$("\$gcc_candidate" -dumpfullversion -dumpversion | cut -d. -f1)"
  gxx_major="\$("\$gxx_candidate" -dumpfullversion -dumpversion | cut -d. -f1)"
  [[ "\$gcc_major" = 12 && "\$gxx_major" = 12 ]] || {
    printf 'CUDA host compiler must be GCC/G++ 12; found GCC %s and G++ %s\n' \
      "\$gcc_major" "\$gxx_major" >&2
    return 1
  }
  export CC="\$gcc_candidate"
  export CXX="\$gxx_candidate"
  export NVCC_CCBIN="\$gxx_candidate"
}
trap cleanup EXIT INT TERM
mkdir -p "\$stage"
tar -xf - -C "\$stage"
cd "\$stage"
mkdir -p \
  "\$stage/home" \
  "\$stage/cache/xdg" \
  "\$stage/cache/huggingface" \
  "\$stage/cache/torch" \
  "\$stage/cache/triton" \
  "\$stage/cache/flashinfer" \
  "\$stage/cache/tmp"
mkdir -p logs
export TMPDIR="\$stage/cache/tmp"
export HOME="\$stage/home"
export XDG_CACHE_HOME="\$stage/cache/xdg"
export HF_HOME="\$stage/cache/huggingface"
export HUGGINGFACE_HUB_CACHE="\$HF_HOME/hub"
export TRANSFORMERS_CACHE="\$HF_HOME/transformers"
export TORCH_HOME="\$stage/cache/torch"
export TRITON_CACHE_DIR="\$stage/cache/triton"
export FLASHINFER_WORKSPACE_BASE="\$stage/cache/flashinfer"
export VLLM_USE_FLASHINFER_SAMPLER="0"
export VLLM_ALLREDUCE_USE_FLASHINFER="0"
export VIRTUAL_ENV="\$stage/vllm-runtime"
export PATH="\$VIRTUAL_ENV/bin:\$PATH"
export PYTHONPATH="\$stage"
export RTLSPEC_EVAL_API_KEY=local-lora-eval
unset CC CXX NVCC_CCBIN CUDAHOSTCXX
compiler_probe_enabled=0
if [[ -n "\${requested_host_gcc:-}" || -n "\${requested_host_gxx:-}" ]]; then
  select_cuda_host_compiler
  compiler_probe_enabled=1
fi
if [[ "\$compiler_probe_enabled" = 1 ]]; then
  if ! {
  printf 'CC=%s\n' "\$CC"
  printf 'CXX=%s\n' "\$CXX"
  printf 'NVCC_CCBIN=%s\n' "\$NVCC_CCBIN"
  printf 'TMPDIR=%s\n' "\$TMPDIR"
  "\$CC" --version
  "\$CXX" --version
  nvcc --version
  cat > "\$TMPDIR/compiler-probe.cu" <<'CU'
__global__ void compiler_probe_kernel() {}

int main() {
  return 0;
}
CU
  nvcc -std=c++17 "\$TMPDIR/compiler-probe.cu" -o "\$TMPDIR/compiler-probe"
  "\$TMPDIR/compiler-probe"
  } > logs/cuda-host-compiler-probe.log 2>&1
  then
    printf 'CUDA host compiler probe failed\n' >&2
    tail -n 200 logs/cuda-host-compiler-probe.log >&2 || true
    exit 1
  fi
else
  printf 'CUDA host compiler probe disabled; FlashInfer paths are disabled.\n' \
    > logs/cuda-host-compiler-probe.log
fi
if ! "\$vllm_python" - <<'PY' \
  > logs/vllm-runtime-probe.log 2>&1
import os
import pathlib
import sys
import vllm

print("python_executable:", sys.executable)
print("python_version:", sys.version)
print("vllm_version:", vllm.__version__)

for name in (
    "HOME",
    "XDG_CACHE_HOME",
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
    "TRITON_CACHE_DIR",
    "FLASHINFER_WORKSPACE_BASE",
):
    value = os.environ.get(name)
    print(f"{name}:", value)
    if not value:
        raise RuntimeError(f"{name} is not configured")
    path = pathlib.Path(value)
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()

for name in (
    "VLLM_USE_FLASHINFER_SAMPLER",
    "VLLM_ALLREDUCE_USE_FLASHINFER",
):
    value = os.environ.get(name)
    print(f"{name}:", value)
    if value != "0":
        raise RuntimeError(f"{name} must be 0")

from flashinfer.jit import env as flashinfer_env

print("flashinfer_base_dir:", flashinfer_env.FLASHINFER_BASE_DIR)
print("flashinfer_cache_dir:", flashinfer_env.FLASHINFER_CACHE_DIR)
print("flashinfer_workspace_dir:", flashinfer_env.FLASHINFER_WORKSPACE_DIR)
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
  --attention-backend FLASH_ATTN \
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
if [[ "\$startup_only" = 1 ]]; then
  stop_vllm
  tar -cf - models.json logs/vllm-runtime-probe.log logs/finetune-cpe-lora-eval-vllm.log
  exit 0
fi
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
tar -cf - '$CANDIDATE' '$RAW_OUTPUTS' '$EVAL_RUN' '${REPORT_PREFIX}_comparison.md' '${REPORT_PREFIX}_comparison.json' '${REPORT_PREFIX}_vs_base_diff.md' '${REPORT_PREFIX}_vs_base_diff.json' '${REPORT_PREFIX}_acceptance.md' '${REPORT_PREFIX}_acceptance.json' models.json logs/cuda-host-compiler-probe.log logs/vllm-runtime-probe.log logs/candidate-generation.json logs/finetune-cpe-lora-eval-vllm.log logs/finetune-cpe-lora-eval-run.log logs/evaluation-command.json logs/comparison-command.json logs/difference-command.json logs/acceptance-command.json
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
