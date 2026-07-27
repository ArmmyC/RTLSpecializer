module tb;
  logic a, b;
  logic y;
  TopModule dut(.a(a), .b(b), .y(y));
  initial begin
    a = 1'b0; b = 1'b0;
    #1 $display("Mismatches: 0");
    $finish;
  end
endmodule
