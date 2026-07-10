from pathlib import Path
import re
import subprocess
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
        if "printf" in line:
            assert ">&2" in line
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
    assert 'import vllm' in probe
    assert 'vllm.__version__' in probe
    assert "printf 'staged vLLM runtime probe failed\\n' >&2" in probe
    assert "tail -n 200 logs/vllm-runtime-probe.log >&2 || true" in probe
    assert "exit 1" in probe

    archive = remote[remote.index("\ntar -cf -") :]
    assert "logs/vllm-runtime-probe.log" in archive.splitlines()[1]


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
