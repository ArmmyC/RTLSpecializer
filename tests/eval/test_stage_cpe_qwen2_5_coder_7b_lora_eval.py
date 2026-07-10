from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"scripts/eval/stage_cpe_qwen2_5_coder_7b_lora_eval.sh"

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
    remote=text.split("remote=$(cat <<EOF", 1)[1].split("EOF\n)", 1)[0]
    for line in remote.splitlines():
        if "--json" in line:
            assert ">" in line
    assert "printf" not in remote
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
