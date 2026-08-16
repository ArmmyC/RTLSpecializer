from __future__ import annotations

from scripts.dataset.author_rtl_generation_assetfix_retry import (
    PUBLIC_FIXTURES,
    PUBLIC_TESTBENCHES,
)
from scripts.dataset.rtl_generation_batch_corrections import (
    is_qualification_retry_selection,
    static_testbench_audit,
)


def test_retry_selection_requires_explicit_parent_binding():
    base = {
        "selection_kind": "qualification_retry",
        "parent_run_id": "pilot",
        "parent_qualification_report_sha256": "report",
        "parent_qualification_evidence_sha256": "evidence",
        "parent_qualified_subset_binding_sha256": "binding",
        "retry_reason": "public oracle defect",
    }
    assert is_qualification_retry_selection(base)
    missing = dict(base)
    del missing["parent_qualification_evidence_sha256"]
    assert not is_qualification_retry_selection(missing)
    assert not is_qualification_retry_selection({"selection_kind": "regular_batch"})


def test_retry_assets_are_reference_free_and_static():
    assert tuple(PUBLIC_TESTBENCHES) == tuple(PUBLIC_FIXTURES)
    for source_id, testbench in PUBLIC_TESTBENCHES.items():
        audit, errors = static_testbench_audit(testbench.encode("utf-8"))
        assert errors == [], (source_id, errors)
        assert audit["candidate_instantiation_count"] == 1
        assert audit["canonical_result_format_found"] is True
        assert audit["support_file_count"] == 0
        assert tuple(PUBLIC_FIXTURES[source_id]) == (
            "positive",
            "constant_zero",
            "wrong_neighbor_direction",
        ) if source_id in {"Prob092_gatesv100", "Prob094_gatesv"} else tuple(PUBLIC_FIXTURES[source_id]) == (
            "positive",
            "constant_zero",
            "wrong_signal",
        ) if source_id == "Prob101_circuit4" else tuple(PUBLIC_FIXTURES[source_id]) == (
            "positive",
            "constant_zero",
            "highest_bit_priority",
        )
        for fixture in PUBLIC_FIXTURES[source_id].values():
            lowered = fixture.casefold()
            assert "refmodule" not in lowered
            assert "reference.sv" not in lowered
            assert "/home/" not in lowered
            assert "/tmp/" not in lowered
