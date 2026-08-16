from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.rtl_generation_batch_selection import BATCH20_SOURCE_IDS
from scripts.dataset.rtl_generation_qualification_failure_analysis import (
    analyze_qualification_failures,
)


FAILED = ["Prob092_gatesv100", "Prob112_always_case2", "Prob115_shift18", "Prob109_fsm1"]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_failure_analysis_is_metadata_only_and_conservative(tmp_path: Path) -> None:
    run_root = tmp_path / "pilot_004_assetfix_v003"
    report_rows = []
    case_rows = []
    evidence_rows = []
    qualified = []
    failed = []
    case_number = 0
    for source_id in BATCH20_SOURCE_IDS:
        task_id = f"task_{source_id}"
        is_failed = source_id in FAILED
        report_rows.append({
            "source_id": source_id,
            "task_id": task_id,
            "qualification_status": "positive_candidate_failed" if is_failed else "qualified",
        })
        (failed if is_failed else qualified).append(task_id)
        for kind, mutation_name in (
            ("positive", "public_spec_candidate"),
            ("negative", "negative_one"),
            ("negative", "negative_two"),
        ):
            case_number += 1
            case_id = f"case_{case_number:03d}"
            candidate_id = f"candidate_{case_number:03d}"
            candidate_hash = f"{case_number:064x}"[-64:]
            testbench_hash = f"{(case_number + 100):064x}"[-64:]
            case_rows.append({
                "qualification_case_id": case_id,
                "source_id": source_id,
                "task_id": task_id,
                "candidate_id": candidate_id,
                "candidate_sha256": candidate_hash,
                "testbench_sha256": testbench_hash,
                "kind": kind,
                "mutation_name": mutation_name,
                "expected_outcome": "accepted" if kind == "positive" else "rejected",
            })
            is_undetected = source_id == "Prob115_shift18" and mutation_name == "negative_one"
            positive_failed = is_failed and source_id != "Prob115_shift18" and kind == "positive"
            evidence_rows.append({
                "qualification_case_id": case_id,
                "source_id": source_id,
                "task_id": task_id,
                "candidate_id": candidate_id,
                "candidate_sha256": candidate_hash,
                "testbench_sha256": testbench_hash,
                "kind": kind,
                "compile_passed": True,
                "simulation_attempted": True,
                "simulation_passed": not positive_failed and not (kind == "negative" and not is_undetected),
                "reported_mismatch_counts": [3 if positive_failed or (kind == "negative" and not is_undetected) else 0],
                "maximum_mismatch_count": 3 if positive_failed or (kind == "negative" and not is_undetected) else 0,
                "timeout_reported": False,
                "observed_outcome": "positive_candidate_failed" if positive_failed else ("mutation_not_detected" if is_undetected else ("mutation_detected" if kind == "negative" else "passed")),
                "failure_category": "functional_mismatch" if positive_failed or (kind == "negative" and not is_undetected) else "passed",
            })
    # The implementation uses source IDs for the qualified list, matching the
    # persisted qualification artifact used by the analysis CLI.
    (run_root / "reports").mkdir(parents=True)
    (run_root / "qualification" / "attempt_01" / "staged").mkdir(parents=True)
    _write_json(run_root / "reports" / "asset_qualification_validation.json", {
        "schema_version": "rtl_asset_qualification_report_v0.1",
        "run_id": run_root.name,
        "selected_tasks": 20,
        "source_commit": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "selection_ids_sha256": "c" * 64,
        "correction_manifest_sha256": "d" * 64,
        "rows": report_rows,
    })
    (run_root / "reports" / "asset_qualification_evidence.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in evidence_rows),
        encoding="utf-8",
    )
    (run_root / "qualification" / "attempt_01" / "case_manifest.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in case_rows),
        encoding="utf-8",
    )
    (run_root / "qualification" / "attempt_01" / "qualified_task_ids.txt").write_text(
        "".join(f"{task_id}\n" for task_id in qualified), encoding="utf-8"
    )
    (run_root / "qualification" / "attempt_01" / "failed_or_inconclusive_task_ids.txt").write_text(
        "".join(f"task_{source_id}\n" for source_id in FAILED), encoding="utf-8"
    )
    _write_json(run_root / "qualification" / "attempt_01" / "staged" / "candidate_evidence.jsonl.runner.json", {})

    output = run_root / "reports" / "qualification_failure_analysis.json"
    result, code = analyze_qualification_failures(run_root=run_root, output_path=output)

    assert code == 0, result
    assert result["ok"] is True
    value = json.loads(output.read_text(encoding="utf-8"))
    assert all(row["classification"] == "inconclusive" for row in value["rows"])
    assert value["reference_inspected"] is False
    assert value["rerun"] is False
    assert "/home/" not in output.read_text(encoding="utf-8")
    assert "reference.sv" not in output.read_text(encoding="utf-8")
