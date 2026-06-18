module ve_counter_tb;
  logic clk, rst_n, en;
  logic [3:0] count_q;
  ve_counter dut(.clk(clk), .rst_n(rst_n), .en(en), .count_q(count_q));
endmodule
