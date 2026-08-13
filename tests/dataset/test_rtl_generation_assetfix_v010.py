"""Static regression tests for the assetfix_v010 retry catalog."""

from __future__ import annotations

from scripts.dataset.author_rtl_generation_assetfix_v010 import (
    MUTATION_NAMES,
    PUBLIC_FIXTURES,
    PUBLIC_TESTBENCHES,
    SOURCE_IDS,
)
from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit
from scripts.dataset.rtl_generation_asset_qualification import (
    _normalize_qualification_correction,
)


def test_v010_is_exactly_the_two_diagnosed_asset_tasks() -> None:
    assert SOURCE_IDS == (
        "Prob149_ece241_2013_q4",
        "Prob139_2013_q2bfsm",
    )


def test_v010_testbenches_are_standalone_and_canonical() -> None:
    for source_id in SOURCE_IDS:
        audit, errors = static_testbench_audit(PUBLIC_TESTBENCHES[source_id].encode("utf-8"))
        assert errors == [], (source_id, errors)
        assert audit["support_file_count"] == 0
        assert audit["module_declarations"] == ["tb"]
        assert audit["candidate_instantiation_count"] == 1
        assert PUBLIC_TESTBENCHES[source_id].count('Mismatches: %0d') == 1


def test_v010_prob139_public_sequence_reaches_g_after_three_bits() -> None:
    positive = PUBLIC_FIXTURES["Prob139_2013_q2bfsm"]["positive"]
    assert "X1: state <= x ? X1 : X10;" in positive
    assert "X10: state <= x ? G1 : MONITOR;" in positive
    assert "X101" not in positive


def test_v010_prob149_preserves_declared_public_interface() -> None:
    testbench = PUBLIC_TESTBENCHES["Prob149_ece241_2013_q4"]
    positive = PUBLIC_FIXTURES["Prob149_ece241_2013_q4"]["positive"]
    assert "reg [3:1] s" in testbench
    assert ".fr3(fr3)" in testbench
    assert ".fr1(fr1)" in testbench
    assert "output logic [3:1]" not in positive
    assert "input logic [3:1] s" in positive
    assert "output logic fr3" in positive
    assert "fr0" not in testbench + positive


def test_v010_fixture_catalog_has_two_negative_mutations_per_task() -> None:
    for source_id in SOURCE_IDS:
        assert list(PUBLIC_FIXTURES[source_id]) == [
            "positive",
            *MUTATION_NAMES[source_id],
        ]


def test_v010_compact_manifest_normalizes_without_mutating_input() -> None:
    source_id = SOURCE_IDS[0]
    original = {
        "correction_version": "assetfix_v010",
        "fixture_hashes": {
            "no_dfr": "a" * 64,
            "positive": "b" * 64,
            "wrong_flow_levels": "c" * 64,
        },
        "original_prompt_sha256": "d" * 64,
        "source_id": source_id,
    }

    normalized = _normalize_qualification_correction(original)

    assert original == {
        "correction_version": "assetfix_v010",
        "fixture_hashes": {
            "no_dfr": "a" * 64,
            "positive": "b" * 64,
            "wrong_flow_levels": "c" * 64,
        },
        "original_prompt_sha256": "d" * 64,
        "source_id": source_id,
    }
    assert normalized["testbench_path"] == (
        f"tasks/{source_id}/testbench.sv"
    )
    assert normalized["verification_readiness"] == (
        "pending_qualification"
    )
    assert normalized["upstream_commit"] == (
        "c498220d0a52248f8e3fdffe279075215bde2da6"
    )
    assert normalized["source_tree_sha256"] == (
        "e88d556887c67467147757ae4bb772836ae0224e313581785e8c705fe56f304f"
    )
    assert normalized["frozen_split_sha256"] == (
        "6675f0dc7369c0bff167d56d0e1e5f4ea50f3b94cf9a6d94eae0e6478321f608"
    )
    assert [
        contract["name"] for contract in normalized["mutation_contracts"]
    ] == ["public_spec_candidate", "no_dfr", "wrong_flow_levels"]
