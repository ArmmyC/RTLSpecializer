#!/usr/bin/env bash
# Stage the LoRA pilot runtime onto a CPE L40 node that cannot see shared home.
set -euo pipefail

DATASET_RELATIVE_DIR="outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical"
OUTPUT_RELATIVE_DIR="outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora"
SMOKE_OUTPUT_RELATIVE_DIR="outputs/finetune/qwen2_5_coder_7b_rtl_teacher_distill_lora_smoke"
MODEL_STAGE_RELATIVE_DIR="models/Qwen__Qwen2.5-Coder-7B-Instruct"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: scripts/finetune/stage_cpe_lora.sh [options]

Run this on the CPE login host. It streams the finetune scripts, canonical dataset,
and Python site-packages into /tmp on an L40 node before running checks there.

Options:
  --train                 After checks and dry-run, stage the local base model and train.
  --smoke-train           Run one real training step and save separate smoke artifacts.
  --max-steps N           Use N positive training steps with --train; default uses epochs.
  --job-id ID             Reuse an existing Slurm allocation with srun --overlap.
  --source-root PATH      CPE checkout/archive extraction root (default: ~/RTLSpecializer).
  --model-source-dir PATH Local Qwen model directory on the CPE login host.
  --keep-stage            Keep the /tmp stage directory after the GPU command exits.
  -h, --help              Show this help text.

Without --train or --smoke-train the launcher only runs the GPU environment
check and trainer dry-run. Both training modes refuse to overwrite their
persistent adapter output.
EOF
}

run_staged() {
  local stage_root="$1"
  local run_training="$2"
  local keep_stage="$3"
  local output_relative_dir="$4"
  local max_steps="$5"
  local site_packages="$stage_root/.venv_site_packages"
  local dataset_dir="$stage_root/$DATASET_RELATIVE_DIR"
  local output_dir="$stage_root/$output_relative_dir"

  if [[ "$keep_stage" != "1" ]]; then
    trap 'rm -rf -- "$stage_root"' EXIT
  fi

  export HOME="$stage_root/home"
  export XDG_CACHE_HOME="$stage_root/cache"
  export HF_HOME="$stage_root/cache/huggingface"
  export HUGGINGFACE_HUB_CACHE="$stage_root/cache/huggingface/hub"
  export TRANSFORMERS_CACHE="$stage_root/cache/huggingface/transformers"
  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1
  export PYTHONPATH="$site_packages${PYTHONPATH:+:$PYTHONPATH}"
  mkdir -p "$HOME" "$HF_HOME" "$HUGGINGFACE_HUB_CACHE" "$TRANSFORMERS_CACHE"
  cd "$stage_root"

  printf '=== Staged Runtime ===\n' >&2
  printf 'stage_root=%s\n' "$stage_root" >&2
  printf 'site_packages=%s\n' "$site_packages" >&2
  printf '=== GPU Environment Check ===\n' >&2
  python3 scripts/finetune/check_training_environment.py \
    --dataset-dir "$dataset_dir" \
    --expected-gpu-substring L40 \
    --json >&2

  printf '=== Trainer Dry Run ===\n' >&2
  python3 scripts/finetune/train_qwen2_5_coder_7b_lora.py \
    --dataset-dir "$dataset_dir" \
    --output-dir "$output_dir" \
    --expected-gpu-substring L40 \
    --dry-run \
    --json >&2

  if [[ "$run_training" != "1" ]]; then
    printf 'Checks completed. Re-run with --train to start LoRA training.\n' >&2
    return 0
  fi

  local staged_model_dir="$stage_root/$MODEL_STAGE_RELATIVE_DIR"
  [[ -d "$staged_model_dir" ]] || die "staged base model is missing: $staged_model_dir"

  printf '=== LoRA Training ===\n' >&2
  python3 scripts/finetune/train_qwen2_5_coder_7b_lora.py \
    --base-model "$staged_model_dir" \
    --dataset-dir "$dataset_dir" \
    --output-dir "$output_dir" \
    --expected-gpu-substring L40 \
    --max-steps "$max_steps" \
    --json >&2

  # stdout is reserved for this archive so the login host can persist it safely.
  tar -C "$stage_root" -cf - "$output_relative_dir"
}

if [[ "${1:-}" == "--run-staged" ]]; then
  shift
  [[ "$#" -eq 5 ]] || die "internal staged invocation requires stage root and mode flags"
  run_staged "$@"
  exit 0
fi

source_root="${CPE_RTLSPECIALIZER_ROOT:-$HOME/RTLSpecializer}"
model_source_dir="${CPE_QWEN_MODEL_DIR:-$HOME/LLMModel/qwen25-coder-7b-instruct/models/Qwen__Qwen2.5-Coder-7B-Instruct}"
job_id=""
run_training=0
keep_stage=0
smoke_train=0
max_steps=-1

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --train)
      run_training=1
      ;;
    --smoke-train)
      run_training=1
      smoke_train=1
      ;;
    --max-steps)
      [[ "$#" -ge 2 ]] || die "--max-steps requires a value"
      [[ "$2" =~ ^[1-9][0-9]*$ ]] || die "--max-steps must be a positive integer"
      max_steps="$2"
      shift
      ;;
    --job-id)
      [[ "$#" -ge 2 ]] || die "--job-id requires a value"
      job_id="$2"
      shift
      ;;
    --source-root)
      [[ "$#" -ge 2 ]] || die "--source-root requires a value"
      source_root="$2"
      shift
      ;;
    --model-source-dir)
      [[ "$#" -ge 2 ]] || die "--model-source-dir requires a value"
      model_source_dir="$2"
      shift
      ;;
    --keep-stage)
      keep_stage=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

if [[ "$smoke_train" == "1" ]]; then
  if [[ "$max_steps" != "-1" && "$max_steps" != "1" ]]; then
    die "--smoke-train always uses exactly one training step"
  fi
  max_steps=1
  output_relative_dir="$SMOKE_OUTPUT_RELATIVE_DIR"
elif [[ "$run_training" == "1" ]]; then
  output_relative_dir="$OUTPUT_RELATIVE_DIR"
elif [[ "$max_steps" != "-1" ]]; then
  die "--max-steps requires --train; use --smoke-train for the one-step compatibility check"
else
  output_relative_dir="$OUTPUT_RELATIVE_DIR"
fi

command -v srun >/dev/null 2>&1 || die "srun is required; run this launcher on the CPE login host"
source_root="$(cd "$source_root" && pwd -P)" || die "source root not found: $source_root"
[[ -d "$source_root/$DATASET_RELATIVE_DIR" ]] || die "canonical dataset not found under: $source_root/$DATASET_RELATIVE_DIR"
[[ -f "$source_root/scripts/finetune/check_training_environment.py" ]] || die "finetune scripts not found under: $source_root"

shopt -s nullglob
site_package_candidates=("$source_root"/.venv/lib/python*/site-packages)
shopt -u nullglob
[[ "${#site_package_candidates[@]}" -eq 1 ]] || die "expected exactly one .venv/lib/python*/site-packages under $source_root"
site_package_relative_path="${site_package_candidates[0]#"$source_root/"}"
site_package_transform="s,^$site_package_relative_path,.venv_site_packages,"

if [[ "$run_training" == "1" ]]; then
  [[ -d "$model_source_dir" ]] || die "base model directory not found: $model_source_dir"
  persistent_output_dir="$source_root/$output_relative_dir"
  if [[ -e "$persistent_output_dir" || -L "$persistent_output_dir" ]]; then
    if [[ -L "$persistent_output_dir" ]] || [[ ! -d "$persistent_output_dir" ]]; then
      die "adapter output must be an absent directory or an empty real directory: $persistent_output_dir"
    fi
    if [[ -n "$(find "$persistent_output_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
      die "refusing to overwrite existing adapter output: $persistent_output_dir"
    fi
  fi
fi

run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
stage_root="/tmp/rtlspecializer-lora-${USER:-unknown}-$run_id"
artifact_archive="$(mktemp "${TMPDIR:-/tmp}/rtlspecializer-lora-artifacts.XXXXXX.tar")"
cleanup_local() {
  rm -f -- "$artifact_archive"
}
trap cleanup_local EXIT

srun_args=(
  --chdir=/tmp
)
if [[ "$smoke_train" == "1" ]]; then
  srun_args+=(--job-name=rtlspecializer-lora-smoke)
else
  srun_args+=(--job-name=rtlspecializer-lora-stage)
fi
if [[ -n "$job_id" ]]; then
  srun_args+=(--jobid="$job_id" --overlap)
else
  if [[ "$run_training" == "1" ]]; then
    requested_time="12:00:00"
    if [[ "$smoke_train" == "1" ]]; then
      requested_time="01:00:00"
    fi
    srun_args+=(
      --partition=gpul40
      --gres=gpu:1
      --cpus-per-task=8
      --mem=64G
      --time="$requested_time"
    )
  else
    srun_args+=(
      --partition=gpul40
      --gres=gpu:1
      --cpus-per-task=4
      --mem=24G
      --time=00:15:00
    )
  fi
fi

printf 'Staging into %s on the GPU node.\n' "$stage_root"
if [[ "$run_training" == "1" ]]; then
  if [[ "$smoke_train" == "1" ]]; then
    printf 'One-step smoke training is enabled; artifacts will be restored under %s.\n' "$source_root/$output_relative_dir"
  else
    printf 'Training is enabled; completed adapter artifacts will be restored under %s.\n' "$source_root/$output_relative_dir"
  fi
else
  printf 'Training is disabled; only environment check and dry-run will execute.\n'
fi

if [[ "$run_training" == "1" ]]; then
  tar -C "$source_root" -cf - \
    --transform="$site_package_transform" \
    scripts/finetune \
    "$DATASET_RELATIVE_DIR" \
    "$site_package_relative_path" \
    -C "$model_source_dir" \
    --transform="s,^\\./,$MODEL_STAGE_RELATIVE_DIR/," \
    . \
    | srun "${srun_args[@]}" /bin/bash -lc \
      "rm -rf -- '$stage_root'; mkdir -p -- '$stage_root'; tar -xf - -C '$stage_root'; exec bash '$stage_root/scripts/finetune/stage_cpe_lora.sh' --run-staged '$stage_root' '$run_training' '$keep_stage' '$output_relative_dir' '$max_steps'" \
      >"$artifact_archive"
else
  tar -C "$source_root" -cf - \
    --transform="$site_package_transform" \
    scripts/finetune \
    "$DATASET_RELATIVE_DIR" \
    "$site_package_relative_path" \
    | srun "${srun_args[@]}" /bin/bash -lc \
      "rm -rf -- '$stage_root'; mkdir -p -- '$stage_root'; tar -xf - -C '$stage_root'; exec bash '$stage_root/scripts/finetune/stage_cpe_lora.sh' --run-staged '$stage_root' '$run_training' '$keep_stage' '$output_relative_dir' '$max_steps'" \
      >"$artifact_archive"
fi

if [[ "$run_training" == "1" ]]; then
  tar -C "$source_root" -xf "$artifact_archive"
  printf 'Training completed; adapter artifacts restored to %s.\n' "$source_root/$output_relative_dir"
else
  [[ ! -s "$artifact_archive" ]] || die "dry-run unexpectedly returned artifact data"
  printf 'CPE environment check and trainer dry-run completed.\n'
fi
