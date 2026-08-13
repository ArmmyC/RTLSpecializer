from scripts.dataset.author_rtl_generation_assetfix_v009 import (
    PUBLIC_FIXTURES,
    PUBLIC_TESTBENCHES,
    _validate_fixture,
)
from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    static_testbench_audit,
)


SOURCE_IDS = (
    "Prob007_wire",
    "Prob012_xnorgate",
    "Prob019_m2014_q4f",
    "Prob052_gates100",
    "Prob059_wire4",
    "Prob065_7420",
    "Prob081_7458",
    "Prob098_circuit7",
    "Prob108_rule90",
    "Prob124_rule110",
    "Prob131_mt2015_q4",
    "Prob129_ece241_2013_q8",
    "Prob135_m2014_q6b",
    "Prob137_fsm_serial",
    "Prob138_2012_q2fsm",
    "Prob139_2013_q2bfsm",
    "Prob146_fsm_serialdata",
    "Prob148_2013_q2afsm",
    "Prob150_review2015_fsmonehot",
    "Prob154_fsm_ps2data",
)


def test_v009_public_catalog_is_complete_and_static_safe() -> None:
    assert set(PUBLIC_TESTBENCHES) >= set(SOURCE_IDS)
    assert set(PUBLIC_FIXTURES) >= set(SOURCE_IDS)

    for source_id in SOURCE_IDS:
        audit, errors = static_testbench_audit(
            PUBLIC_TESTBENCHES[source_id].encode("utf-8")
        )
        assert errors == [], (source_id, errors)
        assert audit["support_file_count"] == 0
        assert list(PUBLIC_FIXTURES[source_id]) == [
            "positive",
            *MUTATION_NAMES[source_id],
        ]
        for name, content in PUBLIC_FIXTURES[source_id].items():
            assert _validate_fixture(source_id, name, content) == []
