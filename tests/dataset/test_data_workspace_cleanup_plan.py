from __future__ import annotations

import json
from pathlib import Path

from scripts.dataset.data_workspace import (
    collect_data_workspace_inventory,
    plan_data_workspace_cleanup,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _task(source_id: str) -> dict:
    return {
        "schema_version": "rtl_task.v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "prompt": f"Review {source_id}",
        "artifacts": {"rtl_code": "module dut; endmodule\n"},
    }


def _answer(source_id: str) -> dict:
    return {
        "schema_version": "rtl_answer.v0.1",
        "source_id": source_id,
        "task_type": "rtl_bug_review",
        "issue_summary": [],
        "time_reasoning": {},
        "space_reasoning": {},
        "safe_optimization": {},
        "functional_risk": [],
        "verification_plan": [],
        "claim_levels": {
            "correctness": "suggestion_only",
            "area": "insufficient_evidence",
            "activity": "insufficient_evidence",
            "power": "insufficient_evidence",
        },
        "evidence_used": [],
        "limitations": [],
    }


def _inventory_and_plan(tmp_path: Path, *, apply: bool = False) -> tuple[dict, dict]:
    data_dir = tmp_path / "data"
    inventory_json = data_dir / "reports" / "data_workspace_inventory.json"
    plan_json = data_dir / "reports" / "data_workspace_cleanup_plan.json"
    inventory, code = collect_data_workspace_inventory(data_dir=data_dir, output_json=inventory_json)
    assert code == 0, inventory
    plan, code = plan_data_workspace_cleanup(
        data_dir=data_dir,
        inventory_json=inventory_json,
        plan_json=plan_json,
        apply=apply,
        dry_run=not apply,
    )
    assert code == 0, plan
    return inventory, plan


def test_cleanup_planner_proposes_expected_destinations(tmp_path) -> None:
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "review" / "rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl", [_task("rtlcoder_001")])
    _write_json(
        data_dir / "review" / "rtlcoder_teacher_answer_returns_1000" / "batch_001_answers_rtl_answer_v0_1.json",
        {"answers": [_answer("rtlcoder_001")]},
    )
    _write_json(
        data_dir / "review" / "repaired_rtl_answer_batches" / "rtlcoder_teacher_answer_returns_1000" / "batch_001_answers_rtl_answer_v0_1.json",
        {"answers": [_answer("rtlcoder_001")]},
    )
    _write_jsonl(data_dir / "review" / "rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl", [_answer("rtlcoder_001")])
    _write_json(data_dir / "review" / "rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json", {"ok": True})

    _, plan = _inventory_and_plan(tmp_path)
    moves = {move["old_path"]: move for move in plan["proposed_moves"]}

    assert moves["review/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl"]["new_path"] == "normalized/tasks/rtlcoder_rtl_task_v0_1_synthetic_bug_draft_1000.jsonl"
    assert moves["review/rtlcoder_teacher_answer_returns_1000/batch_001_answers_rtl_answer_v0_1.json"]["new_path"] == "answers/teacher_returns/rtlcoder_synthetic/batch_001_answers_rtl_answer_v0_1.json"
    assert moves["review/repaired_rtl_answer_batches/rtlcoder_teacher_answer_returns_1000/batch_001_answers_rtl_answer_v0_1.json"]["new_path"] == "answers/repaired/rtlcoder_synthetic/batch_001_answers_rtl_answer_v0_1.json"
    assert moves["review/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl"]["new_path"] == "answers/assembled/rtlcoder_synthetic_rtl_answer_v0_1_assembled.jsonl"
    assert moves["review/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json"]["new_path"] == "reports/assembly/rtlcoder_synthetic_rtl_answer_v0_1_assembly_report.json"


def test_cleanup_planner_does_not_overwrite_existing_destination(tmp_path) -> None:
    data_dir = tmp_path / "data"
    source = data_dir / "review" / "tasks.jsonl"
    existing = data_dir / "normalized" / "tasks" / "tasks.jsonl"
    _write_jsonl(source, [_task("row_001")])
    _write_jsonl(existing, [_task("row_existing")])

    _, plan = _inventory_and_plan(tmp_path)
    move = next(move for move in plan["proposed_moves"] if move["old_path"] == "review/tasks.jsonl")

    assert move["new_path"].startswith("normalized/tasks/tasks__")
    assert move["manual_review_needed"] is True


def test_cleanup_planner_does_not_move_low_or_medium_confidence_files_on_apply(tmp_path) -> None:
    data_dir = tmp_path / "data"
    raw_tree = data_dir / ".local_data" / "verilog-eval-main" / "README.md"
    raw_tree.parent.mkdir(parents=True, exist_ok=True)
    raw_tree.write_text("raw tree\n", encoding="utf-8")

    _, plan = _inventory_and_plan(tmp_path, apply=True)

    assert raw_tree.exists()
    move = next(move for move in plan["proposed_moves"] if move["old_path"] == ".local_data/verilog-eval-main/README.md")
    assert move["confidence"] == "medium"
    assert move["applied"] is False
    assert move["status"] == "planned_only"


def test_cleanup_planner_does_not_delete_files(tmp_path) -> None:
    data_dir = tmp_path / "data"
    task_path = data_dir / "review" / "tasks.jsonl"
    _write_jsonl(task_path, [_task("row_001")])
    before = sum(1 for path in data_dir.rglob("*") if path.is_file())

    _, plan = _inventory_and_plan(tmp_path)
    after = sum(1 for path in data_dir.rglob("*") if path.is_file())

    assert task_path.exists()
    assert after >= before
    assert plan["applied_move_count"] == 0


def test_gitkeep_paths_are_allowed_by_gitignore() -> None:
    text = Path(".gitignore").read_text(encoding="utf-8")

    assert "!/data/README.md" in text
    assert "!/data/**/.gitkeep" in text or "!/data/reports/inventory/.gitkeep" in text
