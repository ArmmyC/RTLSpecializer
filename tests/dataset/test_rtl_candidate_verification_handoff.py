from __future__ import annotations

import os
import stat
from pathlib import Path

from tests.dataset.manual_rtl_teacher_helpers import FIXTURE_ROOT, initial_flow


def test_handoff_contains_candidate_testbench_support_but_no_reference(tmp_path: Path) -> None:
    _, _, run = initial_flow(tmp_path)
    assert (run / "candidate_manifest.jsonl").is_file()
    assert (run / "verification_plan.jsonl").is_file()
    assert (run / "workspace/rtlgen_synthetic_example_attempt_01/candidate.sv").is_file()
    assert (run / "workspace/rtlgen_synthetic_example_attempt_01/testbench.sv").is_file()
    assert (run / "workspace/rtlgen_synthetic_example_attempt_01/support/helper.svh").is_file()
    assert not list(run.rglob("reference.sv"))
    reference = (FIXTURE_ROOT / "private_assets/workspace/rtlgen_synthetic_example/reference.sv").read_bytes()
    assert all(reference not in path.read_bytes() for path in run.rglob("*") if path.is_file())
    instructions = (run / "run_instructions.md").read_text(encoding="utf-8")
    assert "rtlbench verify-candidates" in instructions
    assert "did not execute RTLBench" in instructions


def test_handoff_directories_are_private_under_permissive_umask(tmp_path: Path) -> None:
    previous_umask = os.umask(0o022)
    try:
        _, _, run = initial_flow(tmp_path)
    finally:
        os.umask(previous_umask)

    for path in (run, *sorted(run.rglob("*"))):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            assert mode == 0o700, (path, oct(mode))
        elif path.is_file():
            assert mode == 0o600, (path, oct(mode))
