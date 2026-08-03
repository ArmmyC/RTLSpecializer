from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.dataset.rtl_generation_batch_selection import (
    INVENTORY_SCHEMA_VERSION,
    SELECTION_SCHEMA_VERSION,
    select_batch_rows,
    sha256_file,
)
from scripts.dataset.rtl_generation_human_review import (
    HUMAN_REVIEW_SCHEMA_VERSION,
    package_tree_sha256,
    validate_human_review,
)
from scripts.dataset.rtl_generation_dataset import SMOKE_TASK_IDS
from scripts.dataset.rtl_generation_inventory import SPLIT_SCHEMA_VERSION


COMMIT = "a" * 40
TREE_HASH = "b" * 64


def _write_inventory(path: Path, source_ids: list[str]) -> None:
    rows = []
    for source_id in source_ids:
        rows.append({
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "source_id": source_id,
            "source_dataset": "VerilogEval",
            "source_commit": COMMIT,
            "license": "MIT",
            "design_family": "rtl",
            "task_id": f"task_{source_id}",
            "source_prompt_sha256": hashlib.sha256(source_id.encode()).hexdigest(),
            "reference_rtl_sha256": "c" * 64,
            "testbench_sha256": "d" * 64,
            "support_file_hashes": [],
            "top_module": "TopModule",
            "interface_deterministic": True,
            "clock_signal_count": 0,
            "reset_signal_count": 0,
            "behavior_categories": ["combinational"],
            "verification_readiness": "needs_testbench",
            "readiness_reasons": [
                "testbench depends on a non-candidate module that is available only in private reference material",
            ],
            "duplicate_group": None,
            "missing_files": [],
            "unresolved_dependency_count": 1,
            "unresolved_package_count": 0,
            "dependency_analysis_ambiguous": False,
            "testbench_top_module_count": 1,
            "warning_codes": [],
        })
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _write_split(path: Path, inventory: Path, source_ids: list[str]) -> None:
    path.write_text(json.dumps({
        "schema_version": SPLIT_SCHEMA_VERSION,
        "inventory_sha256": sha256_file(inventory),
        "seed": 7,
        "splits": {"train": source_ids, "validation": [], "test": []},
        "eligible_generation_pool": {"source_ids": {"train": [], "validation": [], "test": []}},
    }) + "\n", encoding="utf-8")


def _write_acquisition(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version": "rtl_source_acquisition_v0.1",
        "repository": "NVlabs/verilog-eval",
        "source_commit": COMMIT,
        "source_tree_sha256": TREE_HASH,
        "dirty_checkout": False,
        "row_count": 156,
        "errors": [],
    }) + "\n", encoding="utf-8")


def test_batch_selection_is_deterministic_and_hash_bound(tmp_path: Path) -> None:
    source_ids = [f"Prob{i:03d}" for i in range(1, 4)]
    inventory = tmp_path / "inventory.jsonl"
    split = tmp_path / "split.json"
    acquisition = tmp_path / "acquisition.json"
    ids_output = tmp_path / "ids.txt"
    report_output = tmp_path / "selection.json"
    _write_inventory(inventory, source_ids)
    _write_split(split, inventory, source_ids)
    _write_acquisition(acquisition)

    roles = {source_id: "bounded" for source_id in source_ids}
    tags = {source_id: ("combinational_logic",) for source_id in source_ids}
    report, code = select_batch_rows(
        inventory,
        split,
        acquisition,
        ids_output,
        report_output,
        source_ids=source_ids,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE_HASH,
        expected_inventory_sha256=sha256_file(inventory),
        expected_split_sha256=sha256_file(split),
        expected_count=3,
        selection_roles=roles,
        diversity_tags=tags,
        required_targets={"combinational_logic": 3},
        advisory_targets={},
        excluded_source_ids=[],
    )

    assert code == 0
    assert report["ok"] is True
    assert report["schema_version"] == SELECTION_SCHEMA_VERSION
    assert report["selected_count"] == 3
    assert ids_output.read_text(encoding="utf-8").splitlines() == source_ids
    assert report["selection_ids_sha256"] == hashlib.sha256(ids_output.read_bytes()).hexdigest()

    _, second_code = select_batch_rows(
        inventory,
        split,
        acquisition,
        ids_output,
        tmp_path / "second.json",
        source_ids=source_ids,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE_HASH,
        expected_inventory_sha256=sha256_file(inventory),
        expected_split_sha256=sha256_file(split),
        expected_count=3,
        selection_roles=roles,
        diversity_tags=tags,
        required_targets={"combinational_logic": 3},
        advisory_targets={},
        excluded_source_ids=[],
    )
    assert second_code == 1


def test_batch_selection_rejects_validation_or_test_ids(tmp_path: Path) -> None:
    source_ids = ["Prob001", "Prob002", "Prob003"]
    inventory = tmp_path / "inventory.jsonl"
    split = tmp_path / "split.json"
    acquisition = tmp_path / "acquisition.json"
    _write_inventory(inventory, source_ids)
    _write_split(split, inventory, source_ids[:2])
    _write_acquisition(acquisition)
    roles = {source_id: "bounded" for source_id in source_ids}
    tags = {source_id: ("combinational_logic",) for source_id in source_ids}

    _, code = select_batch_rows(
        inventory,
        split,
        acquisition,
        tmp_path / "ids.txt",
        tmp_path / "selection.json",
        source_ids=source_ids,
        expected_source_commit=COMMIT,
        expected_source_tree_sha256=TREE_HASH,
        expected_inventory_sha256=sha256_file(inventory),
        expected_split_sha256=sha256_file(split),
        expected_count=3,
        selection_roles=roles,
        diversity_tags=tags,
        required_targets={"combinational_logic": 3},
        advisory_targets={},
        excluded_source_ids=[],
    )
    assert code == 1


def _write_review_fixture(tmp_path: Path) -> tuple[Path, Path]:
    package = tmp_path / "package"
    package.mkdir()
    rows = []
    review_rows = []
    for index, (source_id, task_id) in enumerate(zip(
        ("Prob001_zero", "Prob020_mt2015_eq2", "Prob071_always_casez", "Prob048_m2014_q4c", "Prob079_fsm3onehot"),
        SMOKE_TASK_IDS,
    ), 1):
        candidate_id = f"{task_id}_attempt_01"
        candidate_hash = hashlib.sha256(f"rtl-{index}".encode()).hexdigest()
        rows.append({
            "source_id": source_id,
            "task_id": task_id,
            "candidate_id": candidate_id,
            "candidate_sha256": candidate_hash,
        })
        review_rows.append({
            **rows[-1],
            "specification_preserved": True,
            "interface_correct": True,
            "rtl_behavior_consistent": True,
            "training_quality_acceptable": True,
            "private_content_detected": False,
            "assistant_rtl_only": True,
            "metadata_accurate": True,
            "review_exception_acknowledged": False,
            "review_outcome": "approved_for_experimental_training",
            "review_notes": "Reviewed against the public task and interface.",
        })
    (package / "all.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "package_id": "rtl_generation_smoke_v001_retry_02",
        "promotion_allowed": False,
    }) + "\n", encoding="utf-8")
    tree_hash = package_tree_sha256(package)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "schema_version": HUMAN_REVIEW_SCHEMA_VERSION,
        "package_id": "rtl_generation_smoke_v001_retry_02",
        "package_tree_sha256": tree_hash,
        "reviewer": {
            "reviewer_type": "human",
            "reviewer_id": "reviewer@example",
            "reviewed_at": "2026-08-03T00:00:00Z",
            "review_method": "manual_row_review",
        },
        "review_status": "human_reviewed",
        "overall_decision": "approved_for_experimental_training",
        "promotion_allowed": False,
        "row_count": 5,
        "rows": review_rows,
        "review_exceptions": [],
    }) + "\n", encoding="utf-8")
    return package, review


def test_human_review_requires_real_reviewer_and_matches_package(tmp_path: Path) -> None:
    package, review = _write_review_fixture(tmp_path)
    result, code = validate_human_review(
        package,
        review,
        expected_package_tree_sha256=package_tree_sha256(package),
    )
    assert code == 0
    assert result["human_review_complete"] is True

    value = json.loads(review.read_text(encoding="utf-8"))
    value["reviewer"]["reviewer_type"] = "automated_agent"
    review.write_text(json.dumps(value) + "\n", encoding="utf-8")
    result, code = validate_human_review(
        package,
        review,
        expected_package_tree_sha256=package_tree_sha256(package),
    )
    assert code == 1
    assert any("reviewer_type" in error for error in result["errors"])


def test_human_review_rejects_unknown_record_fields(tmp_path: Path) -> None:
    package, review = _write_review_fixture(tmp_path)
    value = json.loads(review.read_text(encoding="utf-8"))
    value["unexpected"] = "must fail closed"
    review.write_text(json.dumps(value) + "\n", encoding="utf-8")

    result, code = validate_human_review(
        package,
        review,
        expected_package_tree_sha256=package_tree_sha256(package),
    )

    assert code == 1
    assert any("invalid field set" in error for error in result["errors"])
