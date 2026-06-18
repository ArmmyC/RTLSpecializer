module fixture_shift(input logic clk, rst, serial_in, output logic [7:0] data_q);
  always_ff @(posedge clk) begin
    if (rst) data_q <= '0;
    else data_q <= {serial_in, data_q[7:1]};
  end
endmodule
