from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY_ROOT / "scripts/finetune/stage_cpe_lora.sh"


def test_launcher_is_valid_bash() -> None:
    result = subprocess.run(
        ["bash", "-n", str(LAUNCHER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_launcher_help_does_not_require_cpe_commands() -> None:
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--train" in result.stdout
    assert "--job-id ID" in result.stdout


def test_launcher_defaults_to_checks_and_requires_explicit_train() -> None:
    contents = LAUNCHER.read_text(encoding="utf-8")
    assert 'run_training=0' in contents
    assert '--train)\n      run_training=1' in contents
    assert 'check_training_environment.py' in contents
    assert '--dry-run' in contents
    assert 'HF_HUB_OFFLINE=1' in contents
    assert 'TRANSFORMERS_OFFLINE=1' in contents
    assert 'refusing to overwrite existing adapter output' in contents
    assert 'adapter output must be an absent directory or an empty real directory' in contents
    assert '--time=00:15:00' in contents
    assert '--time=12:00:00' in contents


def test_launcher_stages_runtime_dataset_and_model_for_training() -> None:
    contents = LAUNCHER.read_text(encoding="utf-8")
    assert 'DATASET_RELATIVE_DIR="outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical"' in contents
    assert 'site_package_candidates=("$source_root"/.venv/lib/python*/site-packages)' in contents
    assert 'site_package_transform="s,^$site_package_relative_path,.venv_site_packages,"' in contents
    assert 'MODEL_STAGE_RELATIVE_DIR="models/Qwen__Qwen2.5-Coder-7B-Instruct"' in contents
    assert 'tar -C "$source_root" -cf -' in contents
    assert "mkdir -p -- '$stage_root'; tar -xf - -C '$stage_root'" in contents
    assert 'tar -C "$source_root" -xf "$artifact_archive"' in contents


def test_dry_run_payload_normalizes_site_packages_path(tmp_path) -> None:
    source_root = tmp_path / "RTLSpecializer"
    finetune_dir = source_root / "scripts" / "finetune"
    finetune_dir.mkdir(parents=True)
    shutil.copy2(LAUNCHER, finetune_dir / LAUNCHER.name)
    shutil.copy2(
        REPOSITORY_ROOT / "scripts/finetune/check_training_environment.py",
        finetune_dir / "check_training_environment.py",
    )

    dataset_dir = source_root / "outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical"
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "train.jsonl").write_text("{}\n", encoding="utf-8")

    package_dir = source_root / ".venv/lib/python3.12/site-packages/torch"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    captured_payload = tmp_path / "payload.txt"
    fake_srun = bin_dir / "srun"
    fake_srun.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ntar -tf - > \"$CAPTURED_PAYLOAD\"\n",
        encoding="utf-8",
    )
    fake_srun.chmod(0o755)

    environment = dict(os.environ)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    environment["CAPTURED_PAYLOAD"] = str(captured_payload)
    environment["TMPDIR"] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--source-root", str(source_root)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    staged_paths = captured_payload.read_text(encoding="utf-8")
    assert "scripts/finetune/stage_cpe_lora.sh" in staged_paths
    assert "outputs/finetune_datasets/rtlcoder_synthetic_teacher_distill_v0_1_canonical/train.jsonl" in staged_paths
    assert ".venv_site_packages/torch/__init__.py" in staged_paths
    assert ".venv/lib/python3.12/site-packages/torch/__init__.py" not in staged_paths
