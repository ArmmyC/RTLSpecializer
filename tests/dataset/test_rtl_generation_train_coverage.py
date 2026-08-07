from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.dataset.rtl_generation_dataset import (
    GENERATION_SFT_SCHEMA_VERSION,
    PACKAGE_SCHEMA_VERSION,
    SYSTEM_PROMPT,
)
from scripts.dataset.rtl_generation_train_coverage import (
    assemble_train_union,
    package_tree_sha256,
    sha256_file,
    validate_train_coverage,
)
from scripts.dataset.rtl_generation_preparation import GENERATION_TASK_SCHEMA_VERSION


COMMIT = "a" * 40
TREE = "b" * 64


def _task(source_id: str, task_id: str) -> dict:
    return {
        "schema_version": GENERATION_TASK_SCHEMA_VERSION,
        "task_id": task_id,
        "source_id": source_id,
        "source_dataset": "VerilogEval",
        "design_family": "synthetic",
        "language": "systemverilog",
        "specification": "Implement TopModule with output zero tied low.",
        "top_module": "TopModule",
        "interface": {
            "ports": [
                {
                    "name": "zero",
                    "direction": "output",
                    "declaration": "output zero",
                    "packed_range": None,
                    "width_bits": 1,
                    "signed": False,
                    "description": None,
                }
            ]
        },
        "clocking": {"clock_signal": None, "edge": None},
        "reset": {"signal": None, "active_level": None, "synchronous": None},
        "latency_contract": None,
        "behavioral_constraints": [],
        "assumptions": [],
        "ambiguities": [],
        "provenance": {
            "public_dataset_name": "VerilogEval",
            "public_dataset_url": "https://example.invalid/verilog-eval",
            "source_commit": COMMIT,
            "license": "MIT",
            "original_source_id": source_id,
        },
    }


def _row(source_id: str, task_number: int) -> dict:
    task_id = f"rtlgen_{source_id.lower()}_{task_number:03d}"
    task = _task(source_id, task_id)
    rtl = f"module TopModule(output logic zero); assign zero = 1'b0; endmodule // {task_number}\n"
    candidate_hash = hashlib.sha256(rtl.encode("utf-8")).hexdigest()
    candidate_id = f"{task_id}_attempt_01"
    checks = {
        "compile": {"candidate": {"attempted": True, "passed": True, "reason": None}},
        "simulation": {"candidate_passes": {"attempted": True, "passed": True, "reason": None}},
        "lint": {"candidate": {"attempted": False, "passed": None, "reason": "not_requested"}},
        "synthesis": {"candidate": {"attempted": False, "passed": None, "reason": "not_requested"}},
    }
    attempt = {
        "schema_version": "rtl_generation_attempt_v0.1",
        "candidate_id": candidate_id,
        "task_id": task_id,
        "source_id": source_id,
        "attempt": 1,
        "top_module": "TopModule",
        "candidate_sha256": candidate_hash,
        "verification_profile": "verilog_eval_mismatch_v1",
        "accepted": True,
        "failure_category": "passed",
        "checks": checks,
        "mismatch_summary": {
            "contract": "mismatch_count_v1",
            "reported_counts": [0],
            "reported_sample_counts": [1],
            "maximum_count": 0,
            "timeout_reported": False,
        },
        "diagnostics": [],
        "toolchain": {
            "iverilog": {"available": True, "version": "synthetic"},
            "vvp": {"available": True, "version": "synthetic"},
            "verilator": {"available": False, "version": None},
            "yosys": {"available": False, "version": None},
        },
    }
    candidate_record = {
        "schema_version": "rtl_teacher_candidate_record_v0.1",
        "candidate_id": candidate_id,
        "task_id": task_id,
        "source_id": source_id,
        "attempt": 1,
        "packet_id": f"packet_{task_number:03d}",
        "candidate_sha256": candidate_hash,
        "candidate": {
            "schema_version": "rtl_teacher_candidate_v0.1",
            "task_id": task_id,
            "top_module": "TopModule",
            "language": "systemverilog",
            "rtl": rtl,
            "implementation_summary": "Drives zero low.",
            "assumptions": [],
        },
    }
    return {
        "schema_version": GENERATION_SFT_SCHEMA_VERSION,
        "id": f"verified_rtl_generation_{candidate_id}",
        "dataset_name": "verilog_eval_verified_generation_v0_1",
        "dataset_version": "v0.1",
        "dataset_stage": "verified_generation_sft",
        "split": "train",
        "source_id": source_id,
        "task_id": task_id,
        "candidate_id": candidate_id,
        "attempt": 1,
        "candidate_sha256": candidate_hash,
        "source": "teacher_generated_verified",
        "license": "MIT",
        "design_family": "synthetic",
        "created_by": "test",
        "review_status": "automated_verified_unreviewed",
        "approval_status": "not_approved",
        "promotion_allowed": False,
        "provenance": {
            "public_dataset_name": "VerilogEval",
            "public_dataset_url": "https://example.invalid/verilog-eval",
            "source_commit": COMMIT,
            "original_source_id": source_id,
            "notes": "verified",
        },
        "verification": {
            "profile": "verilog_eval_mismatch_v1",
            "accepted": True,
            "compile_passed": True,
            "simulation_passed": True,
            "maximum_mismatch_count": 0,
            "evidence_sha256": "c" * 64,
            "runner_profile": "pilot-docker",
            "rtlbench_commit": COMMIT,
            "candidate_hash_matched": True,
            "qualification_passed": True,
            "qualification_source": "synthetic",
            "assetfix_version": "assetfix_test",
            "reference_supplied": False,
            "warnings_present": False,
            "warning_count": 0,
            "acceptance_affected": False,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
            {"role": "assistant", "content": rtl},
        ],
        "_candidate_record": candidate_record,
        "_attempt": attempt,
    }


def _write_package(root: Path, rows: list[dict], split_hash: str) -> None:
    root.mkdir(mode=0o700)
    public_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in rows]
    (root / "all.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in public_rows), encoding="utf-8")
    (root / "train.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in public_rows), encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": root.name,
        "source_commit": COMMIT,
        "source_tree_sha256": TREE,
        "frozen_split_sha256": split_hash,
        "all_rows": len(public_rows),
        "train_rows": len(public_rows),
        "rejected_rows": 0,
        "promotion_allowed": False,
    }, sort_keys=True) + "\n", encoding="utf-8")


def _write_split(path: Path, rows: list[dict]) -> str:
    value = {
        "schema_version": "rtl_generation_split_v0.1",
        "row_count": len(rows),
        "counts": {"train": len(rows), "validation": 0, "test": 0},
        "splits": {"train": [row["source_id"] for row in rows], "validation": [], "test": []},
        "rows": [
            {"source_id": row["source_id"], "task_id": row["task_id"], "split": "train"}
            for row in rows
        ],
    }
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return sha256_file(path)


def test_coverage_reports_complete_union_and_package_hashes(tmp_path: Path) -> None:
    rows = [_row("ProbA", 1), _row("ProbB", 2)]
    split = tmp_path / "split.json"
    split_hash = _write_split(split, rows)
    package_a = tmp_path / "package_a"
    package_b = tmp_path / "package_b"
    _write_package(package_a, rows[:1], split_hash)
    _write_package(package_b, rows[1:], split_hash)
    report, code = validate_train_coverage(
        [package_a, package_b],
        split,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE,
        expected_split_sha256=split_hash,
        expected_train_count=2,
    )
    assert code == 0, report
    assert report["complete"] is True
    assert report["covered_source_ids"] == ["ProbA", "ProbB"]
    assert report["packages"][0]["tree_sha256"] == package_tree_sha256(package_a)


def test_coverage_rejects_duplicate_and_missing_sources(tmp_path: Path) -> None:
    rows = [_row("ProbA", 1), _row("ProbB", 2)]
    split = tmp_path / "split.json"
    split_hash = _write_split(split, rows)
    package = tmp_path / "package"
    _write_package(package, [rows[0]], split_hash)
    report, code = validate_train_coverage(
        [package, package],
        split,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE,
        expected_split_sha256=split_hash,
        expected_train_count=2,
    )
    assert code == 1
    assert "ProbB" in report["remaining_source_ids"]
    assert "ProbA" in report["duplicate_source_ids"]


def test_coverage_rejects_validation_or_test_contamination(tmp_path: Path) -> None:
    rows = [_row("ProbA", 1)]
    split = tmp_path / "split.json"
    split_hash = _write_split(split, rows)
    package = tmp_path / "package"
    contaminated = json.loads(json.dumps(rows[0]))
    contaminated["split"] = "test"
    _write_package(package, [contaminated], split_hash)
    report, code = validate_train_coverage(
        [package],
        split,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE,
        expected_split_sha256=split_hash,
        expected_train_count=1,
    )
    assert code == 1
    assert any("not a training row" in error for error in report["errors"])


def test_coverage_rejects_manifest_hash_drift_and_private_content(tmp_path: Path) -> None:
    rows = [_row("ProbA", 1)]
    split = tmp_path / "split.json"
    split_hash = _write_split(split, rows)
    package = tmp_path / "package"
    _write_package(package, rows, split_hash)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    manifest["source_tree_sha256"] = "e" * 64
    (package / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    report, code = validate_train_coverage(
        [package],
        split,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE,
        expected_split_sha256=split_hash,
        expected_train_count=1,
    )
    assert code == 1
    assert any("source tree SHA-256 mismatch" in error for error in report["errors"])


def test_union_preflight_is_noncanonical_and_orders_frozen_train_rows(tmp_path: Path) -> None:
    rows = [_row("ProbA", 1), _row("ProbB", 2)]
    split = tmp_path / "split.json"
    split_hash = _write_split(split, rows)
    package_a = tmp_path / "package_a"
    package_b = tmp_path / "package_b"
    _write_package(package_a, rows[:1], split_hash)
    _write_package(package_b, rows[1:], split_hash)
    output = tmp_path / "preflight"
    result, code = assemble_train_union(
        [package_b, package_a],
        split,
        output,
        package_id="union_test",
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE,
        expected_split_sha256=split_hash,
        expected_train_count=2,
    )
    assert code == 0, result
    assert result["published"] is True
    assert result["row_count"] == 2
    assert [json.loads(line)["source_id"] for line in (output / "all.jsonl").read_text().splitlines()] == ["ProbA", "ProbB"]
    assert (output / "validation_report.json").is_file()


def test_union_refuses_nonempty_existing_output(tmp_path: Path) -> None:
    rows = [_row("ProbA", 1)]
    split = tmp_path / "split.json"
    split_hash = _write_split(split, rows)
    package = tmp_path / "package"
    _write_package(package, rows, split_hash)
    output = tmp_path / "union"
    output.mkdir()
    (output / "sentinel").write_text("do not replace", encoding="utf-8")
    result, code = assemble_train_union(
        [package],
        split,
        output,
        package_id="union_test",
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE,
        expected_split_sha256=split_hash,
        expected_train_count=1,
    )
    assert code == 1
    assert result["published"] is False
    assert (output / "sentinel").read_text(encoding="utf-8") == "do not replace"
