from __future__ import annotations

from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

from scripts.dataset.io_utils import load_jsonl
from scripts.dataset.prepare_review_packet import prepare_review_packet
from scripts.dataset.promote_reviewed_rows import main as promote_main
from scripts.dataset.review_promotion import PromotionConfig, is_stub_answer, promote_rows
from scripts.dataset.validation import validate_dataset_file
from tests.dataset.conftest import write_rows


ROOT = Path(__file__).resolve().parents[2]
DRAFT_ROWS = ROOT / "tests" / "fixtures" / "public_review" / "draft_rows.jsonl"
REVIEWED_ROWS = ROOT / "tests" / "fixtures" / "public_review" / "reviewed_rows.jsonl"


def _first_row(path: Path) -> dict:
    rows, problems = load_jsonl(path)
    assert not problems
    return rows[0][1]


def test_review_packet_generation_creates_expected_files(tmp_path) -> None:
    result, code = prepare_review_packet(DRAFT_ROWS, tmp_path / "review")
    assert code == 0
    assert result["packet_rows"] == 1
    assert (tmp_path / "review" / "README.md").exists()
    manifest = tmp_path / "review" / "review_manifest.jsonl"
    assert manifest.exists()
    entry = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
    assert (tmp_path / "review" / entry["review_markdown"]).exists()
    assert (tmp_path / "review" / entry["row_json"]).exists()
    markdown = (tmp_path / "review" / entry["review_markdown"]).read_text(encoding="utf-8")
    assert "imported answers are draft stubs" in markdown
    assert "Issue is visible in supplied artifact" in markdown


def test_review_packet_does_not_mutate_rows(tmp_path) -> None:
    before = DRAFT_ROWS.read_text(encoding="utf-8")
    result, code = prepare_review_packet(DRAFT_ROWS, tmp_path / "review")
    assert code == 0, result
    assert DRAFT_ROWS.read_text(encoding="utf-8") == before
    copied = json.loads((tmp_path / "review" / "rows" / "public_verilog_eval_counter_001.json").read_text(encoding="utf-8"))
    assert copied == json.loads(before.splitlines()[0])


def test_promotion_rejects_unedited_import_stub(tmp_path) -> None:
    row = _first_row(DRAFT_ROWS)
    assert is_stub_answer(row["messages"][2]["content"])
    result, code = promote_rows([row], tmp_path / "out.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 1
    assert result["accepted_rows"] == 0
    assert result["rejected_rows"] == 1
    rejected = json.loads((tmp_path / "out.rejected.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rejected["reason"] == "stub answer"
    assert rejected["row"]["id"] == row["id"]


def test_promotion_accepts_edited_public_row_and_validates(tmp_path) -> None:
    row = _first_row(REVIEWED_ROWS)
    result, code = promote_rows([row], tmp_path / "public_validated_v0.1.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 0, result
    promoted = json.loads((tmp_path / "public_validated_v0.1.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert promoted["review_status"] == "validated"
    assert validate_dataset_file(tmp_path / "public_validated_v0.1.jsonl", strict=True).ok


def test_promotion_rejects_uncertain_license(tmp_path) -> None:
    row = _first_row(REVIEWED_ROWS)
    row["license"] = "unknown"
    result, code = promote_rows([row], tmp_path / "out.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 1
    assert result["rejection_reasons"]["license gate"] == 1


def test_promotion_rejects_missing_public_dataset_provenance(tmp_path) -> None:
    row = _first_row(REVIEWED_ROWS)
    row["provenance"]["public_dataset_name"] = ""
    result, code = promote_rows([row], tmp_path / "out.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 1
    assert result["rejection_reasons"]["provenance gate"] == 1


def test_promotion_rejects_private_source(tmp_path) -> None:
    row = _first_row(REVIEWED_ROWS)
    row["source"] = "handwritten_golden"
    result, code = promote_rows([row], tmp_path / "out.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 1
    assert result["rejection_reasons"]["public source gate"] == 1


def test_promotion_rejects_unsupported_verified_claim(tmp_path) -> None:
    row = _first_row(REVIEWED_ROWS)
    row["messages"][2]["content"]["claim_levels"]["correctness"] = "verified"
    result, code = promote_rows([row], tmp_path / "out.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 1
    rejected = json.loads((tmp_path / "out.rejected.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert any("verified requires a passing simulation or equivalence check" in error for error in rejected["errors"])


def test_promotion_report_json_counts(tmp_path) -> None:
    good = _first_row(REVIEWED_ROWS)
    bad = deepcopy(good)
    bad["id"] = "public_verilog_eval_bad_license"
    bad["license"] = "todo"
    result, code = promote_rows([good, bad], tmp_path / "out.jsonl", tmp_path / "report.json", PromotionConfig())
    assert code == 0, result
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["accepted_rows"] == 1
    assert report["rejected_rows"] == 1
    assert report["by_source"]["public_verilog_eval"] == 1
    assert report["rejection_reasons"]["license gate"] == 1


def test_promotion_cli_json_output_is_parseable(tmp_path) -> None:
    output = tmp_path / "public_validated_v0.1.jsonl"
    report = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/promote_reviewed_rows.py",
            "--input", str(REVIEWED_ROWS), "--output", str(output), "--report", str(report), "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["accepted_rows"] == 1


def test_prepare_review_packet_cli_json_output_is_parseable(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable, "scripts/dataset/prepare_review_packet.py",
            "--input", str(DRAFT_ROWS), "--output-dir", str(tmp_path / "review"), "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["packet_rows"] == 1
