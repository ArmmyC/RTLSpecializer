from pathlib import Path
import re
import subprocess

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
    vllm=tmp_path/"python3"; vllm.write_text("#!/bin/sh\n",encoding="utf-8"); vllm.chmod(0o755)
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
    assert "logs/candidate-generation.json" in archived_paths


def test_readiness_requires_nonempty_models_json_before_exact_alias_check():
    remote = remote_script()
    readiness_loop = remote.index("/v1/models > models.json")
    nonempty_guard = remote.index("[[ -s models.json ]]", readiness_loop)
    json_parse = remote.index("python3 - '$ALIAS' models.json", nonempty_guard)
    alias_check = remote.index("model.get('id') == alias", json_parse)

    assert readiness_loop < nonempty_guard < json_parse < alias_check
    guard = remote[nonempty_guard:json_parse]
    assert "vLLM readiness timed out; see logs/finetune-cpe-lora-eval-vllm.log" in guard
    assert "exit 1" in guard


def test_outer_pipeline_preserves_status_and_extracts_before_returning_it():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert text.splitlines()[2] == "set -euo pipefail"

    pipeline = re.search(
        r'(?ms)^set \+e\n'
        r'(tar -C "\$source_root" .* \| srun .* > "\$artifact")\n'
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
