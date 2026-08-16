from __future__ import annotations

from scripts.dataset.rtl_generation_batch_corrections import static_testbench_audit


def test_static_audit_accepts_public_standalone_contract() -> None:
    content = b'''module tb;
  TopModule dut (.clk(clk));
  initial begin
    $display("Mismatches: %0d", 0);
    $finish;
  end
endmodule
'''
    audit, errors = static_testbench_audit(content)
    assert errors == []
    assert audit["candidate_instantiation_count"] == 1


def test_static_audit_rejects_private_and_extra_dependencies() -> None:
    content = b'''module tb;
  RefModule dut (.x(x));
  initial begin
    $display("Mismatches: %0d", 0);
    $finish;
  end
endmodule
'''
    _, errors = static_testbench_audit(content)
    assert any("private marker" in error for error in errors)


def test_static_audit_rejects_missing_result_contract() -> None:
    content = b'''module tb;
  TopModule dut (.x(x));
  initial $finish;
endmodule
'''
    _, errors = static_testbench_audit(content)
    assert any("canonical mismatch" in error for error in errors)
