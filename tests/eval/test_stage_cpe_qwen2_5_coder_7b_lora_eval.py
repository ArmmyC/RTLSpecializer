from pathlib import Path
import os
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"scripts/eval/stage_cpe_qwen2_5_coder_7b_lora_eval.sh"


def remote_script() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    return text.split("remote=$(cat <<EOF", 1)[1].split("EOF\n)", 1)[0]


def shell_function(text: str, name: str) -> str:
    match = re.search(rf"(?ms)^{name}\(\) \{{\n(.*?)^\}}$", text)
    assert match, f"missing {name} helper"
    return match.group(0)


def runtime_probe_python(remote: str) -> str:
    probe_command = remote.index('if ! "\\$vllm_python" - <<\'PY\'')
    start = remote.index("import os\n", probe_command)
    end = remote.index("\nPY\nthen", start)
    return remote[start:end]


def compiler_probe_shell(remote: str) -> str:
    selection = remote.index("\nselect_cuda_host_compiler\n")
    start = remote.index("if ! {\n", selection)
    end = remote.index("\nfi\n", start) + len("\nfi\n")
    return remote[start:end].replace("\\$", "$")

def test_launcher_is_valid_bash_and_has_fixed_lora_contract():
    assert subprocess.run(["bash","-n",str(SCRIPT)]).returncode==0
    text=SCRIPT.read_text()
    for value in ("--run-eval","qwen2_5_coder_7b_lora_pilot","--enable-lora","--max-lora-rank 16","--lora-modules","--host 127.0.0.1","/v1/models"):
        assert value in text
    assert "preflight only; pass --run-eval" in text

def test_authorizing_spec_exists_and_is_narrow():
    text=(ROOT/"docs/specs/qwen2_5_coder_7b_lora_serving_eval_v0.1.md").read_text()
    assert "Start loopback-only" in text
    assert "Do not train" in text

def test_preflight_applies_value_overrides_without_srun(tmp_path):
    root=tmp_path/"repo"
    required=(
        "data/distill/rtlcoder_synthetic_teacher_distill_v0_1/test.jsonl",
        "docs/eval/rtl_answer_schema_reminder.md",
        "data/eval/candidates/qwen2_5_coder_7b_base_schema_candidates.jsonl",
        "data/eval/runs/rtlcoder_synthetic_rule_baseline/metrics.json",
        "data/eval/runs/rtlcoder_synthetic_active_model_base_schema/metrics.json",
        "data/eval/runs/qwen2_5_coder_7b_base_schema/metrics.json",
        "scripts/eval/run_openai_compatible_candidates.py",
        "scripts/eval/evaluate_answers.py", "scripts/eval/compare_eval_runs.py",
        "scripts/eval/inspect_candidate_differences.py",
        "scripts/eval/check_qwen2_5_coder_7b_lora_acceptance.py",
    )
    for relative in required:
        path=root/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("", encoding="utf-8")
    model=tmp_path/"model"; model.mkdir()
    adapter=tmp_path/"adapter"; adapter.mkdir(); (adapter/"adapter_model.safetensors").write_text("",encoding="utf-8")
    runtime=tmp_path/"runtime"; (runtime/"bin").mkdir(parents=True)
    vllm=runtime/"bin/python3"; vllm.write_text("#!/bin/sh\n",encoding="utf-8"); vllm.chmod(0o755)
    result=subprocess.run(["bash",str(SCRIPT),"--source-root",str(root),"--model-source-dir",str(model),"--adapter-source-dir",str(adapter),"--vllm-python",str(vllm),"--port","8123"],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert f"adapter={adapter}" in result.stdout
    assert "preflight only" in result.stdout

def test_remote_stdout_is_artifact_only_and_rejected_acceptance_is_archived(tmp_path):
    text=SCRIPT.read_text(encoding="utf-8")
    for log in ("logs/evaluation-command.json", "logs/comparison-command.json", "logs/difference-command.json", "logs/acceptance-command.json"):
        assert log in text
    assert "set +e\npython3 scripts/eval/check_qwen2_5_coder_7b_lora_acceptance.py" in text
    assert "tar -cf -" in text
    assert "exit \"\\$acceptance_status\"" in text
    remote=remote_script()
    for line in remote.splitlines():
        if "--json" in line:
            assert ">" in line
        if "printf" in line and ">&2" not in line:
            assert any(
                label in line
                for label in (
                    "CC=%s",
                    "CXX=%s",
                    "NVCC_CCBIN=%s",
                    "TMPDIR=%s",
                    "CUDA host compiler must be",
                )
            )
    assert "echo " not in remote

    stage=tmp_path/"stage"; stage.mkdir(); (stage/"logs").mkdir()
    for name in ("evaluation-command.json", "comparison-command.json", "difference-command.json", "acceptance-command.json"):
        (stage/"logs"/name).write_text('{"ok":false}\n', encoding="utf-8")
    harness='set -euo pipefail\nset +e\nfalse\nacceptance_status=$?\nset -e\ntar -C "$1" -cf - logs\nexit "$acceptance_status"\n'
    result=subprocess.run(["bash","-c",harness,"_",str(stage)],capture_output=True)
    artifact=tmp_path/"artifact.tar"; artifact.write_bytes(result.stdout)
    assert result.returncode == 1
    listed=subprocess.run(["tar","-tf",str(artifact)],capture_output=True,text=True,check=True).stdout
    assert "logs/acceptance-command.json" in listed
    assert "logs/evaluation-command.json" in listed


def test_remote_stops_vllm_after_acceptance_and_before_archiving_evidence():
    remote = remote_script()
    stop = shell_function(remote, "stop_vllm")
    cleanup = shell_function(remote, "cleanup")

    assert re.search(r'\[\[ -n "\\\$\{vllm_pid:-\}" \]\]', stop)
    assert re.search(r'(?m)^    kill "\\\$vllm_pid" 2>/dev/null \|\| true$', stop)
    assert re.search(r'(?m)^    wait "\\\$vllm_pid" 2>/dev/null \|\| true$', stop)
    assert re.search(r'(?m)^    vllm_pid=""$', stop)
    assert re.search(r'(?m)^  stop_vllm$', cleanup)

    acceptance_capture = remote.index("acceptance_status=\\$?")
    explicit_stop = remote.index("\nstop_vllm\n", acceptance_capture)
    archive = remote.index("\ntar -cf -", explicit_stop)
    final_exit = remote.index('exit "\\$acceptance_status"', archive)
    assert acceptance_capture < explicit_stop < archive < final_exit

    tar_command = remote[archive:final_exit]
    archived_paths = set(re.findall(r"(?:^| )('(?:[^']*)'|[^ '\n]+)", tar_command))
    assert "models.json" in archived_paths
    assert "logs/vllm-runtime-probe.log" in archived_paths
    assert "logs/candidate-generation.json" in archived_paths


def test_readiness_requires_nonempty_models_json_before_exact_alias_check():
    remote = remote_script()
    readiness_loop = remote.index("/v1/models\" > models.json")
    nonempty_guard = remote.index("[[ -s models.json ]]", readiness_loop)
    json_parse = remote.index("python3 - '$ALIAS' models.json", nonempty_guard)
    alias_check = remote.index("model.get('id') == alias", json_parse)

    assert readiness_loop < nonempty_guard < json_parse < alias_check
    guard = remote[nonempty_guard:json_parse]
    assert "vLLM readiness response was empty" in guard
    assert "exit 1" in guard


def test_complete_runtime_is_derived_staged_and_selected_remotely():
    text = SCRIPT.read_text(encoding="utf-8")
    remote = remote_script()

    derivation = re.search(
        r'vllm_runtime_source="\$\(cd "\$\(dirname "\$vllm_python"\)/\.\." && pwd -P\)"\n'
        r'\[\[ -x "\$vllm_runtime_source/bin/python3" \]\] \|\| '
        r'die "vLLM runtime interpreter missing: \$vllm_runtime_source/bin/python3"',
        text,
    )
    assert derivation

    staging = shell_function(text, "create_staging_archive")
    assert re.search(
        r'(?m)^  runtime_parent="\$\(dirname "\$vllm_runtime_source"\)"$',
        staging,
    )
    assert re.search(
        r'(?m)^  runtime_name="\$\(basename "\$vllm_runtime_source"\)"$',
        staging,
    )
    assert '-C "$runtime_parent" "$runtime_name"' in staging
    assert '--transform="s,^${runtime_pattern}$,vllm-runtime,"' in staging
    assert '--transform="s,^${runtime_pattern}/,vllm-runtime/,"' in staging
    assert "create_staging_archive | srun " in text
    assert "vllm-site-packages" not in text

    assignment = remote.index(
        'vllm_python="\\$stage/vllm-runtime/bin/python3"'
    )
    virtual_env = remote.index('export VIRTUAL_ENV="\\$stage/vllm-runtime"')
    path_export = remote.index('export PATH="\\$VIRTUAL_ENV/bin:\\$PATH"')
    pythonpath = remote.index('export PYTHONPATH="\\$stage"')
    probe = remote.index('if ! "\\$vllm_python" - <<\'PY\'')
    serve = remote.index(
        '"\\$vllm_python" -m vllm.entrypoints.cli.main serve'
    )
    assert assignment < virtual_env < path_export < pythonpath < probe < serve


def test_runtime_probe_is_private_diagnostic_and_archived():
    remote = remote_script()
    probe_start = remote.index('if ! "\\$vllm_python" - <<\'PY\'')
    probe_end = remote.index("\nfi\n", probe_start) + len("\nfi\n")
    probe = remote[probe_start:probe_end]

    assert re.search(
        r'if ! "\\\$vllm_python" - <<\'PY\' \\\n'
        r'  > logs/vllm-runtime-probe\.log 2>&1',
        probe,
    )
    assert 'import os' in probe
    assert 'import pathlib' in probe
    assert 'import vllm' in probe
    assert 'vllm.__version__' in probe
    assert 'probe.write_text("ok", encoding="utf-8")' in probe
    assert 'probe.unlink()' in probe
    assert 'from flashinfer.jit import env as flashinfer_env' in probe
    assert 'flashinfer_env.FLASHINFER_WORKSPACE_DIR' in probe
    assert "printf 'staged vLLM runtime probe failed\\n' >&2" in probe
    assert "tail -n 200 logs/vllm-runtime-probe.log >&2 || true" in probe
    assert "exit 1" in probe

    archive = remote[remote.index("\ntar -cf -") :]
    assert "logs/vllm-runtime-probe.log" in archive.splitlines()[1]


def test_compiler_selection_and_probe_precede_vllm_and_are_archived():
    text = SCRIPT.read_text(encoding="utf-8")
    remote = remote_script()
    selection = shell_function(remote, "select_cuda_host_compiler")

    assert 'command -v gcc-12 2>/dev/null || true' in selection
    assert 'command -v g++-12 2>/dev/null || true' in selection
    assert re.search(
        r'(?m)^  gcc_major="\\\$\("\\\$gcc_candidate" '
        r'-dumpfullversion -dumpversion \| cut -d\. -f1\)"$',
        selection,
    )
    assert re.search(
        r'(?m)^  gxx_major="\\\$\("\\\$gxx_candidate" '
        r'-dumpfullversion -dumpversion \| cut -d\. -f1\)"$',
        selection,
    )
    assert '[[ "\\$gcc_major" = 12 && "\\$gxx_major" = 12 ]]' in selection
    assert 'export CC="\\$gcc_candidate"' in selection
    assert 'export CXX="\\$gxx_candidate"' in selection
    assert 'export NVCC_CCBIN="\\$gxx_candidate"' in selection
    assert "allow-unsupported-compiler" not in text

    tmpdir = remote.index('export TMPDIR="\\$stage/cache/tmp"')
    select_call = remote.index("\nselect_cuda_host_compiler\n", tmpdir)
    compiler_probe = remote.index("if ! {\n", select_call)
    compiler_log = remote.index(
        "} > logs/cuda-host-compiler-probe.log 2>&1", compiler_probe
    )
    runtime_probe = remote.index('if ! "\\$vllm_python" - <<\'PY\'', compiler_log)
    import_vllm = remote.index("import vllm", runtime_probe)
    serve = remote.index('"\\$vllm_python" -m vllm.entrypoints.cli.main serve')
    assert tmpdir < select_call < compiler_probe < compiler_log < runtime_probe < import_vllm < serve

    probe = remote[compiler_probe:runtime_probe]
    assert 'nvcc --version' in probe
    assert 'nvcc -std=c++17 "\\$TMPDIR/compiler-probe.cu"' in probe
    assert '"\\$TMPDIR/compiler-probe"' in probe
    assert "printf 'CUDA host compiler probe failed\\n' >&2" in probe
    assert "tail -n 200 logs/cuda-host-compiler-probe.log >&2 || true" in probe

    archive_line = remote[remote.index("\ntar -cf -", serve) :].splitlines()[1]
    assert "logs/cuda-host-compiler-probe.log" in archive_line
    assert "$TMPDIR/compiler-probe" not in archive_line
    assert "cache/compiler" not in archive_line
    assert not re.search(r"(?:^| )cache(?:/| |$)", archive_line)


def test_compiler_override_pairing_and_shell_quoting():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--host-gcc", "/tmp/gcc-12"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "both --host-gcc and --host-gxx must be supplied together" in result.stderr

    quote = shell_function(SCRIPT.read_text(encoding="utf-8"), "shell_quote")
    original = "/tmp/compiler path/it's-$gcc[12]"
    quoted = subprocess.run(
        ["bash", "-c", f'{quote}\nshell_quote "$1"', "_", original],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    restored = subprocess.run(
        ["bash", "-c", f'value={quoted}\nprintf "%s" "$value"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert restored == original


def test_fake_gcc12_toolchain_passes_selection_and_cuda_probe(tmp_path):
    remote = remote_script()
    selection = shell_function(remote, "select_cuda_host_compiler").replace(
        "\\$", "$"
    )
    probe = compiler_probe_shell(remote)
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "trace.log"

    compiler_script = """#!/bin/sh
printf '%s:%s\\n' "$(basename "$0")" "$*" >> "$TRACE"
case "$*" in
  *-dumpfullversion*) printf '12.3.0\\n' ;;
  *) printf '%s fake version 12.3.0\\n' "$(basename "$0")" ;;
esac
"""
    for name in ("gcc-12", "g++-12"):
        path = tools / name
        path.write_text(compiler_script, encoding="utf-8")
        path.chmod(0o755)
    nvcc = tools / "nvcc"
    nvcc.write_text(
        """#!/bin/sh
printf 'nvcc:%s\\n' "$*" >> "$TRACE"
if [ "${1:-}" = --version ]; then printf 'nvcc fake version\\n'; exit 0; fi
output=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; output=$1; fi
  shift
done
[ -n "$output" ] || exit 2
printf '#!/bin/sh\\nprintf "compiled-probe-executed\\\\n" >> "$TRACE"\\n' > "$output"
chmod +x "$output"
""",
        encoding="utf-8",
    )
    nvcc.chmod(0o755)

    stage = tmp_path / "stage"
    (stage / "logs").mkdir(parents=True)
    (stage / "cache/tmp").mkdir(parents=True)
    harness = f'''set -euo pipefail
{selection}
requested_host_gcc=""
requested_host_gxx=""
stage=$1
export PATH="$2:$PATH"
export TRACE=$3
export TMPDIR="$stage/cache/tmp"
cd "$stage"
select_cuda_host_compiler
{probe}
printf 'selected_CC=%s\nselected_CXX=%s\nselected_NVCC_CCBIN=%s\n' \
  "$CC" "$CXX" "$NVCC_CCBIN"
'''
    result = subprocess.run(
        ["bash", "-c", harness, "_", str(stage), str(tools), str(trace)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"selected_CC={tools / 'gcc-12'}" in result.stdout
    assert f"selected_CXX={tools / 'g++-12'}" in result.stdout
    assert f"selected_NVCC_CCBIN={tools / 'g++-12'}" in result.stdout

    compiler_log = (stage / "logs/cuda-host-compiler-probe.log").read_text(
        encoding="utf-8"
    )
    assert f"NVCC_CCBIN={tools / 'g++-12'}" in compiler_log
    assert f"TMPDIR={stage / 'cache/tmp'}" in compiler_log
    trace_text = trace.read_text(encoding="utf-8")
    assert "gcc-12:-dumpfullversion -dumpversion" in trace_text
    assert "g++-12:-dumpfullversion -dumpversion" in trace_text
    assert "nvcc:--version" in trace_text
    assert "nvcc:-std=c++17" in trace_text
    assert "compiled-probe-executed" in trace_text


def test_missing_or_wrong_compiler_fails_before_vllm(tmp_path):
    selection = shell_function(
        remote_script(), "select_cuda_host_compiler"
    ).replace("\\$", "$")
    missing = subprocess.run(
        [
            "/usr/bin/bash",
            "-c",
            f'''set -euo pipefail
{selection}
requested_host_gcc=""
requested_host_gxx=""
PATH=$1
if select_cuda_host_compiler; then printf 'vllm-started\n'; fi
exit 1
''',
            "_",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert missing.returncode == 1
    assert "supported GCC 12 compiler was not found" in missing.stderr
    assert "vllm-started" not in missing.stdout

    gcc = tmp_path / "gcc"
    gxx = tmp_path / "gxx"
    gcc.write_text("#!/bin/sh\nprintf '12.2.0\\n'\n", encoding="utf-8")
    gxx.write_text("#!/bin/sh\nprintf '13.1.0\\n'\n", encoding="utf-8")
    gcc.chmod(0o755)
    gxx.chmod(0o755)
    wrong = subprocess.run(
        [
            "/usr/bin/bash",
            "-c",
            f'''set -euo pipefail
{selection}
requested_host_gcc=$1
requested_host_gxx=$2
select_cuda_host_compiler
printf 'vllm-started\n'
''',
            "_",
            str(gcc),
            str(gxx),
        ],
        capture_output=True,
        text=True,
    )
    assert wrong.returncode == 1
    assert "found GCC 12 and G++ 13" in wrong.stderr
    assert "vllm-started" not in wrong.stdout


def test_stage_local_cache_environment_precedes_vllm_import_and_is_not_archived():
    remote = remote_script()
    stage_setup = remote.index('mkdir -p "\\$stage"')
    extraction = remote.index('tar -xf - -C "\\$stage"', stage_setup)
    enter_stage = remote.index('cd "\\$stage"', extraction)
    cache_mkdir = remote.index('  "\\$stage/home" \\', enter_stage)
    probe = remote.index('if ! "\\$vllm_python" - <<\'PY\'', cache_mkdir)
    import_vllm = remote.index("import vllm", probe)

    exports = (
        'export HOME="\\$stage/home"',
        'export XDG_CACHE_HOME="\\$stage/cache/xdg"',
        'export HF_HOME="\\$stage/cache/huggingface"',
        'export HUGGINGFACE_HUB_CACHE="\\$HF_HOME/hub"',
        'export TRANSFORMERS_CACHE="\\$HF_HOME/transformers"',
        'export TORCH_HOME="\\$stage/cache/torch"',
        'export TRITON_CACHE_DIR="\\$stage/cache/triton"',
        'export FLASHINFER_WORKSPACE_BASE="\\$stage/cache/flashinfer"',
    )
    positions = [remote.index(line, cache_mkdir) for line in exports]
    virtual_env = remote.index('export VIRTUAL_ENV="\\$stage/vllm-runtime"')
    assert stage_setup < extraction < enter_stage < cache_mkdir
    assert positions == sorted(positions)
    assert cache_mkdir < positions[0] < positions[-1] < virtual_env < probe < import_vllm

    mkdir_block = remote[cache_mkdir:positions[0]]
    for relative in (
        "home",
        "cache/xdg",
        "cache/huggingface",
        "cache/torch",
        "cache/triton",
        "cache/flashinfer",
    ):
        assert f'"\\$stage/{relative}"' in mkdir_block

    environment_block = remote[enter_stage:probe]
    assert "/storage/slurm/home" not in environment_block
    assert re.findall(r'(?m)^export HOME=(.*)$', environment_block) == [
        '"\\$stage/home"'
    ]

    archive_line = remote[remote.index("\ntar -cf -", probe) :].splitlines()[1]
    assert "logs/vllm-runtime-probe.log" in archive_line
    assert not re.search(r"(?:^| )(?:home|cache)(?:/| |$)", archive_line)


def test_cleanup_removes_stage_local_caches_without_keep(tmp_path):
    remote = remote_script()
    stop = shell_function(remote, "stop_vllm").replace("\\$", "$")
    cleanup = shell_function(remote, "cleanup").replace("\\$", "$")
    stage = tmp_path / "stage"
    (stage / "cache/flashinfer").mkdir(parents=True)
    (stage / "cache/flashinfer/artifact").write_text("cache", encoding="utf-8")
    harness = f'''set -euo pipefail
{stop}
{cleanup}
stage=$1
keep=0
cleanup
[[ ! -e "$stage" ]]
'''
    result = subprocess.run(
        ["bash", "-c", harness, "_", str(stage)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_probe_ignores_unwritable_inherited_home_and_uses_stage_paths(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    inherited_home = tmp_path / "inherited-home"
    inherited_home.mkdir()
    inherited_home.chmod(0)

    (stage / "vllm.py").write_text('__version__ = "0.23.0"\n', encoding="utf-8")
    flashinfer_jit = stage / "flashinfer/jit"
    flashinfer_jit.mkdir(parents=True)
    (stage / "flashinfer/__init__.py").write_text("", encoding="utf-8")
    (flashinfer_jit / "__init__.py").write_text("", encoding="utf-8")
    (flashinfer_jit / "env.py").write_text(
        """import os
from pathlib import Path
FLASHINFER_BASE_DIR = Path(os.environ["FLASHINFER_WORKSPACE_BASE"])
FLASHINFER_CACHE_DIR = FLASHINFER_BASE_DIR / "cache"
FLASHINFER_WORKSPACE_DIR = FLASHINFER_BASE_DIR / "workspace"
""",
        encoding="utf-8",
    )
    probe_file = stage / "probe.py"
    probe_file.write_text(runtime_probe_python(remote_script()), encoding="utf-8")

    harness = r'''set -euo pipefail
stage=$1
mkdir -p \
  "$stage/home" \
  "$stage/cache/xdg" \
  "$stage/cache/huggingface" \
  "$stage/cache/torch" \
  "$stage/cache/triton" \
  "$stage/cache/flashinfer"
export HOME="$stage/home"
export XDG_CACHE_HOME="$stage/cache/xdg"
export HF_HOME="$stage/cache/huggingface"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export TRANSFORMERS_CACHE="$HF_HOME/transformers"
export TORCH_HOME="$stage/cache/torch"
export TRITON_CACHE_DIR="$stage/cache/triton"
export FLASHINFER_WORKSPACE_BASE="$stage/cache/flashinfer"
export PYTHONPATH="$stage"
"$2" "$stage/probe.py"
'''
    environment = os.environ.copy()
    environment["HOME"] = str(inherited_home)
    try:
        result = subprocess.run(
            ["bash", "-c", harness, "_", str(stage), sys.executable],
            capture_output=True,
            text=True,
            env=environment,
        )
    finally:
        inherited_home.chmod(0o700)
    assert result.returncode == 0, result.stderr

    evidence = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition(": ")
        if separator:
            evidence[name] = value
    for name in (
        "HOME",
        "XDG_CACHE_HOME",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "TRITON_CACHE_DIR",
        "FLASHINFER_WORKSPACE_BASE",
        "flashinfer_base_dir",
        "flashinfer_cache_dir",
        "flashinfer_workspace_dir",
    ):
        resolved = Path(evidence[name]).resolve()
        assert resolved.is_relative_to(stage.resolve())
    assert not list(stage.rglob(".write-test"))


def test_readiness_structurally_detects_crash_and_reports_bounded_timeout():
    remote = remote_script()
    loop_start = remote.index("ready=0\n")
    loop_end = remote.index("\n[[ -s models.json ]]", loop_start)
    readiness = remote[loop_start:loop_end]

    assert readiness.startswith("ready=0\nfor _ in \\$(seq 1 120); do\n")
    curl = re.search(
        r'if curl -fsS "http://127\.0\.0\.1:\\\$port/v1/models" '
        r'> models\.json 2>/dev/null; then\n'
        r'    ready=1\n    break\n  fi',
        readiness,
    )
    assert curl

    crash_check = readiness.index('if ! kill -0 "\\$vllm_pid" 2>/dev/null; then')
    crash_wait = readiness.index('wait "\\$vllm_pid" 2>/dev/null || true', crash_check)
    clear_pid = readiness.index('vllm_pid=""', crash_wait)
    crash_error = readiness.index("printf 'vLLM exited before becoming ready\\n' >&2", clear_pid)
    crash_tail = readiness.index(
        "tail -n 200 logs/finetune-cpe-lora-eval-vllm.log >&2 || true",
        crash_error,
    )
    crash_exit = readiness.index("exit 1", crash_tail)
    assert crash_check < crash_wait < clear_pid < crash_error < crash_tail < crash_exit

    timeout_check = readiness.index('if [[ "\\$ready" != 1 ]]; then', crash_exit)
    timeout_error = readiness.index("printf 'vLLM readiness timed out\\n' >&2", timeout_check)
    timeout_tail = readiness.index(
        "tail -n 200 logs/finetune-cpe-lora-eval-vllm.log >&2 || true",
        timeout_error,
    )
    assert timeout_check < timeout_error < timeout_tail


def test_outer_pipeline_preserves_status_and_extracts_before_returning_it():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert text.splitlines()[2] == "set -euo pipefail"

    pipeline = re.search(
        r'(?ms)^set \+e\n'
        r'(create_staging_archive \| srun .* > "\$artifact")\n'
        r'remote_status=\$\?\n'
        r'set -e\n'
        r'\[\[ -s "\$artifact" \]\] && tar -C "\$source_root" -xf "\$artifact"\n'
        r'exit "\$remote_status"$',
        text,
    )
    assert pipeline, "outer staging pipeline/status restoration contract changed"
    assert "|| true" not in pipeline.group(1)
    assert re.search(
        r"trap 'rm -f -- \"\$artifact\"' EXIT", text[: pipeline.start()]
    )


def test_staging_archive_builds_three_distinct_prefixed_trees(tmp_path):
    text = SCRIPT.read_text(encoding="utf-8")
    escape_function = shell_function(text, "escape_tar_transform_regex")
    staging_function = shell_function(text, "create_staging_archive")

    source_root = tmp_path / "repo"
    for name in ("scripts", "data", "docs"):
        directory = source_root / name
        directory.mkdir(parents=True)
        (directory / "placeholder").write_text(name, encoding="utf-8")

    adapter_source = tmp_path / "adapter.[source]"
    adapter_source.mkdir()
    (adapter_source / "adapter_model.safetensors").write_text(
        "adapter", encoding="utf-8"
    )
    model_source = tmp_path / "model+(source)"
    model_source.mkdir()
    (model_source / "config.json").write_text("model", encoding="utf-8")
    runtime_source = tmp_path / "runtime,$source"
    (runtime_source / "bin").mkdir(parents=True)
    runtime_python = runtime_source / "bin/python3"
    runtime_python.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime_python.chmod(0o755)
    archive = tmp_path / "stage.tar"

    harness = f'''set -euo pipefail
{escape_function}
{staging_function}
source_root=$1
adapter_source=$2
model_source=$3
vllm_runtime_source=$4
create_staging_archive > "$5"
'''
    result = subprocess.run(
        [
            "bash",
            "-c",
            harness,
            "_",
            str(source_root),
            str(adapter_source),
            str(model_source),
            str(runtime_source),
            str(archive),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    listing = set(
        subprocess.run(
            ["tar", "-tf", str(archive)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    assert {
        "adapter/adapter_model.safetensors",
        "model/config.json",
        "vllm-runtime/bin/python3",
    } <= listing
    assert {
        "adapter/config.json",
        "adapter/bin/python3",
        "model/bin/python3",
    }.isdisjoint(listing)


def test_stop_logic_reaps_writer_and_archives_stable_evidence(tmp_path):
    remote = remote_script()
    stop = shell_function(remote, "stop_vllm").replace("\\$", "$")
    stage = tmp_path / "stage"
    stage.mkdir()
    harness = f'''set -euo pipefail
{stop}
cd "$1"
mkdir -p logs
: > logs/vllm.log
(while :; do printf 'tick\\n' >> logs/vllm.log; sleep 0.01; done) &
vllm_pid=$!
writer_pid=$vllm_pid
sleep 0.05
stop_vllm
[[ -z "$vllm_pid" ]] || exit 20
if kill -0 "$writer_pid" 2>/dev/null; then exit 21; fi
before=$(wc -c < logs/vllm.log)
sleep 0.05
after=$(wc -c < logs/vllm.log)
[[ "$before" = "$after" ]] || exit 22
printf '{{"data":[{{"id":"qwen2_5_coder_7b_lora_pilot"}}]}}\\n' > models.json
printf '{{"parse_error_rows":0,"api_error_rows":0}}\\n' > logs/candidate-generation.json
tar -cf artifact.tar logs/vllm.log models.json logs/candidate-generation.json
'''
    result = subprocess.run(
        ["bash", "-c", harness, "_", str(stage)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr

    archive = stage / "artifact.tar"
    listing = subprocess.run(
        ["tar", "-tf", str(archive)], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert listing == [
        "logs/vllm.log",
        "models.json",
        "logs/candidate-generation.json",
    ]


def test_readiness_detects_synthetic_server_crash_and_reaps_it(tmp_path):
    stage = tmp_path / "stage"
    runtime_bin = stage / "vllm-runtime/bin"
    runtime_bin.mkdir(parents=True)
    logs = stage / "logs"
    logs.mkdir()
    fake_python = runtime_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\nprintf 'synthetic vLLM traceback\\n' >&2\nexit 42\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    harness = r'''set -euo pipefail
stage=$1
vllm_python="$stage/vllm-runtime/bin/python3"
"$vllm_python" > "$stage/logs/vllm.log" 2>&1 &
vllm_pid=$!
ready=0
waited=0
for _ in $(seq 1 120); do
  if false; then
    ready=1
    break
  fi
  if ! kill -0 "$vllm_pid" 2>/dev/null; then
    wait "$vllm_pid" 2>/dev/null || true
    waited=1
    vllm_pid=""
    printf 'vLLM exited before becoming ready\n' >&2
    tail -n 200 "$stage/logs/vllm.log" >&2 || true
    printf 'waited=%s\nvllm_pid=%s\n' "$waited" "$vllm_pid" > "$stage/state"
    exit 1
  fi
  sleep 0.01
done
exit 99
'''
    started = time.monotonic()
    result = subprocess.run(
        ["bash", "-c", harness, "_", str(stage)],
        capture_output=True,
        text=True,
        timeout=2,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1
    assert elapsed < 2
    assert "vLLM exited before becoming ready" in result.stderr
    assert "synthetic vLLM traceback" in result.stderr
    assert (stage / "state").read_text(encoding="utf-8") == (
        "waited=1\nvllm_pid=\n"
    )
