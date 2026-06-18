module fixture_counter_tb;
  logic clk, rst_n, en;
  logic [3:0] count_q;
  fixture_counter dut(.*);
endmodule
