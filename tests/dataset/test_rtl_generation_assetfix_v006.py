from __future__ import annotations

from scripts.dataset import author_rtl_generation_assetfix_v006 as v006
from scripts.dataset.rtl_generation_batch_corrections import MUTATION_NAMES, static_testbench_audit


def test_v006_catalog_matches_the_pinned_batch_a_order():
    assert len(v006.SOURCE_IDS) == 24
    assert len(set(v006.SOURCE_IDS)) == 24
    assert tuple(v006.PUBLIC_TESTBENCHES) == v006.SOURCE_IDS
    assert tuple(v006.PUBLIC_FIXTURES) == v006.SOURCE_IDS


def test_v006_has_two_public_spec_mutations_per_task():
    for source_id in v006.SOURCE_IDS:
        assert tuple(v006.PUBLIC_FIXTURES[source_id]) == (
            "positive",
            *MUTATION_NAMES[source_id],
        )
        assert all(
            "module TopModule" in value
            for value in v006.PUBLIC_FIXTURES[source_id].values()
        )


def test_v006_testbenches_are_static_and_deterministic():
    for source_id in v006.SOURCE_IDS:
        audit, errors = static_testbench_audit(
            v006.PUBLIC_TESTBENCHES[source_id].encode("utf-8")
        )
        assert errors == [], (source_id, errors)
        assert audit["candidate_instantiation_count"] == 1
        assert audit["canonical_result_format_found"] is True
        assert audit["support_file_count"] == 0
