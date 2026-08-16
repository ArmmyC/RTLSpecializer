from scripts.dataset import author_rtl_generation_assetfix_v008 as authoring
from scripts.dataset.rtl_generation_batch_corrections import (
    MUTATION_NAMES,
    static_testbench_audit,
)


def test_v008_catalog_preserves_frozen_order_and_case_counts() -> None:
    assert tuple(authoring.PUBLIC_TESTBENCHES) == authoring.SOURCE_IDS
    assert tuple(authoring.PUBLIC_FIXTURES) == authoring.SOURCE_IDS
    assert len(authoring.SOURCE_IDS) == 20
    assert len(authoring.RETRY_SOURCE_IDS) == 4

    for source_id in authoring.SOURCE_IDS:
        assert tuple(authoring.PUBLIC_FIXTURES[source_id]) == (
            "positive",
            *MUTATION_NAMES[source_id],
        )


def test_v008_testbenches_pass_static_dependency_audit() -> None:
    for source_id, text in authoring.PUBLIC_TESTBENCHES.items():
        audit, errors = static_testbench_audit(text.encode("utf-8"))
        assert errors == [], (source_id, errors)
        assert audit["candidate_instantiation_count"] == 1
        assert audit["support_file_count"] == 0


def test_v008_fixtures_are_single_public_top_modules() -> None:
    for source_id, fixtures in authoring.PUBLIC_FIXTURES.items():
        assert set(fixtures) == {"positive", *MUTATION_NAMES[source_id]}
        for name, text in fixtures.items():
            lowered = text.casefold()
            assert "refmodule" not in lowered
            assert "reference.sv" not in lowered
            assert "private_assets" not in lowered
            assert "/home/" not in lowered
            assert "/tmp/" not in lowered
            assert text.casefold().count("module topmodule") == 1
            assert text.casefold().count("endmodule") == 1
