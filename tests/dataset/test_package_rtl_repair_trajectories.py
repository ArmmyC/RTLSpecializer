from __future__ import annotations

import hashlib
import json
import stat

from scripts.dataset.package_rtl_repair_trajectories import (
    validate_repair_trajectory_package,
)


def test_repair_trajectory_validator_accepts_public_sanitized_row(tmp_path) -> None:
    failed = "module TopModule; endmodule\n"
    accepted = "module TopModule; assign out = 1'b0; endmodule\n"
    row = {
        "schema_version": "rtl_repair_trajectory_v0.1",
        "task_id": "task_001",
        "source_id": "Prob001",
        "top_module": "TopModule",
        "failure_category_before_repair": "compile_failure",
        "failed_attempt": 1,
        "accepted_attempt": 2,
        "failed_candidate_id": "task_001_attempt_01",
        "failed_candidate_sha256": hashlib.sha256(failed.encode()).hexdigest(),
        "failed_candidate_rtl": failed,
        "accepted_candidate_id": "task_001_attempt_02",
        "accepted_candidate_sha256": hashlib.sha256(accepted.encode()).hexdigest(),
        "accepted_candidate_rtl": accepted,
        "sanitized_diagnostics": ["compile: returncode=4 <source omitted>:1: error"],
        "public_task": {"task_id": "task_001", "source_id": "Prob001"},
    }
    files = {
        "all.jsonl": json.dumps(row) + "\n",
        "manifest.json": json.dumps({
            "schema_version": "rtl_repair_trajectory_package_v0.1",
            "row_count": 1,
            "artifact_bindings": {},
            "promotion_allowed": False,
            "training_allowed": False,
        }) + "\n",
        "statistics.json": json.dumps({"row_count": 1}) + "\n",
        "dataset_card.md": "repair trajectory\n",
        "provenance_report.json": "{}\n",
        "validation_report.json": "{}\n",
        "validation_report.md": "validation\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)

    report, code = validate_repair_trajectory_package(tmp_path, expected_artifacts={})

    assert code == 0
    assert report["ok"] is True
    assert report["private_content_detected"] is False
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in tmp_path.iterdir())
