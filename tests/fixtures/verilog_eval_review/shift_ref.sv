module ve_shift(input logic clk, load, shift_en, input logic [7:0] data_i, output logic [7:0] data_q);
  always_ff @(posedge clk) begin
    if (load) data_q <= data_i;
    else if (shift_en) data_q <= {data_q[6:0], 1'b0};
  end
endmodule
