"""Static regression coverage for the v005 public-specification catalog."""

from scripts.dataset.author_rtl_generation_assetfix_v005 import (
    CORRECTION_VERSION,
    MUTATION_NAMES,
    PUBLIC_FIXTURES,
    PUBLIC_TESTBENCHES,
    SOURCE_IDS,
    _public_rtl_errors,
)
from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit


def test_v005_catalog_is_complete_and_ordered() -> None:
    assert CORRECTION_VERSION == "assetfix_v005"
    assert len(SOURCE_IDS) == 30
    assert len(set(SOURCE_IDS)) == 30
    assert tuple(PUBLIC_FIXTURES) == SOURCE_IDS
    assert tuple(PUBLIC_TESTBENCHES) == SOURCE_IDS

    for source_id in SOURCE_IDS:
        assert tuple(PUBLIC_FIXTURES[source_id]) == (
            "positive",
            *MUTATION_NAMES[source_id],
        )


def test_v005_testbenches_are_reference_free_and_parser_complete() -> None:
    for source_id in SOURCE_IDS:
        text = PUBLIC_TESTBENCHES[source_id]
        _, errors = static_testbench_audit(text.encode("utf-8"))
        assert errors == [], source_id
        assert text.count("module tb") == 1, source_id
        assert text.count("TopModule dut") == 1, source_id
        assert text.count('$display("Mismatches: %0d", mismatch_count)') == 1, source_id
        assert text.count("$finish") == 1, source_id


def test_v005_fixtures_are_single_public_top_modules() -> None:
    for source_id in SOURCE_IDS:
        for name, text in PUBLIC_FIXTURES[source_id].items():
            assert _public_rtl_errors(text) == [], (source_id, name)
            assert text.count("module TopModule") == 1, (source_id, name)
            assert text.count("endmodule") == 1, (source_id, name)


def test_v005_timer_catalog_covers_reset_and_terminal_counting() -> None:
    testbench = PUBLIC_TESTBENCHES["Prob156_review2015_fancytimer"]
    assert "reset" in testbench
    assert "counting" in testbench
    assert "done" in testbench
    assert "999" in testbench
