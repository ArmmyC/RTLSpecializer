"""Static regression tests for the assetfix_v010 retry catalog."""

from __future__ import annotations

from scripts.dataset.author_rtl_generation_assetfix_v010 import (
    MUTATION_NAMES,
    PUBLIC_FIXTURES,
    PUBLIC_TESTBENCHES,
    SOURCE_IDS,
)
from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit


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
