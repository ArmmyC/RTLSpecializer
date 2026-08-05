from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.dataset import rtl_generation_assetfix_v004_retry as retry


def test_public_spec_diagnosis_classifies_prob070_as_exhaustively_equivalent() -> None:
    analysis = retry._semantic_analysis("Prob070_ece241_2013_q2")

    assert analysis["exhaustive_public_input_analysis"] is True
    assert analysis["input_space_size"] == 16
    assert analysis["positive_vs_mutation_difference_count"] == 0
    assert analysis["combinational_loop"] is False


def test_public_spec_diagnosis_classifies_prob074_as_observably_equivalent() -> None:
    analysis = retry._semantic_analysis("Prob074_ece241_2014_q4")

    assert analysis["initial_state"] == [0, 0, 0]
    assert analysis["feedback_is_registered"] is True
    assert analysis["combinational_loop"] is False
    assert analysis["positive_vs_mutation_output_difference"] is False


@pytest.mark.parametrize("source_id", retry.FAILED_SOURCE_IDS)
def test_retry_mutations_are_single_self_contained_top_modules(source_id: str) -> None:
    name, text = retry._replacement_mutation(source_id)

    assert name in {"wrong_pos_assignment", "wrong_or_feedback"}
    assert retry._candidate_static_errors(text) == []
    assert text.count("module TopModule") == 1
    assert "module tb" not in text.casefold()


def test_metadata_safety_rejects_private_absolute_paths() -> None:
    with pytest.raises(retry.RetryPreparationError):
        retry._assert_metadata_safe(json.dumps({"path": "/home/private/file.sv"}))


def test_workspace_tree_hash_is_order_independent_and_content_bound(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    nested = root / "Prob070"
    nested.mkdir(parents=True)
    (nested / "candidate.sv").write_text("module TopModule; endmodule\n", encoding="utf-8")
    root.chmod(0o700)
    nested.chmod(0o700)
    (nested / "candidate.sv").chmod(0o600)

    first = retry._workspace_tree_sha256(root)
    (nested / "candidate.sv").write_text("module TopModule(input logic a); endmodule\n", encoding="utf-8")
    second = retry._workspace_tree_sha256(root)

    assert first != second
