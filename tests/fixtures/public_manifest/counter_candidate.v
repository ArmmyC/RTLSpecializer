module public_counter(input logic clk, rst_n, en, output logic [3:0] count_q);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) count_q <= 4'd0;
    else if (en) count_q <= count_q + 4'd1;
  end
endmodule
