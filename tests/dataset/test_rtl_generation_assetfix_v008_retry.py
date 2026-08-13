from scripts.dataset.author_rtl_generation_assetfix_v008_retry import (
    CLASSIFICATIONS,
    RETRY_FIXTURES,
    RETRY_TESTBENCHES,
    SOURCE_IDS,
)
from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit


def test_retry_order_and_classifications_are_pinned():
    assert SOURCE_IDS == (
        "Prob131_mt2015_q4",
        "Prob135_m2014_q6b",
        "Prob138_2012_q2fsm",
        "Prob139_2013_q2bfsm",
        "Prob146_fsm_serialdata",
        "Prob148_2013_q2afsm",
        "Prob150_review2015_fsmonehot",
        "Prob154_fsm_ps2data",
    )
    assert set(CLASSIFICATIONS) == set(SOURCE_IDS)
    assert CLASSIFICATIONS["Prob131_mt2015_q4"] == "semantically_equivalent_mutation"
    assert CLASSIFICATIONS["Prob139_2013_q2bfsm"] == "positive_fixture_defect"
    assert CLASSIFICATIONS["Prob154_fsm_ps2data"] == "ambiguous_public_specification"


def test_retry_testbenches_are_statically_reference_free():
    for source_id in SOURCE_IDS:
        assert source_id in RETRY_TESTBENCHES
        audit, errors = static_testbench_audit(RETRY_TESTBENCHES[source_id].encode("utf-8"))
        assert errors == [], (source_id, errors)
        assert audit["candidate_instantiation_count"] == 1
        assert audit["canonical_result_format_found"] is True
        assert audit["support_file_count"] == 0


def test_retry_fixtures_have_one_positive_and_two_distinct_negatives():
    assert tuple(RETRY_FIXTURES) == SOURCE_IDS
    for source_id in SOURCE_IDS:
        fixtures = RETRY_FIXTURES[source_id]
        assert list(fixtures) == ["positive", *list(fixtures)[1:]]
        assert len(fixtures) == 3
        assert len({value.encode("utf-8") for value in fixtures.values()}) == 3
        for value in fixtures.values():
            lowered = value.casefold()
            assert "refmodule" not in lowered
            assert "reference.sv" not in lowered
            assert "/home/" not in lowered
            assert "/tmp/" not in lowered
            assert "testbench" not in lowered
