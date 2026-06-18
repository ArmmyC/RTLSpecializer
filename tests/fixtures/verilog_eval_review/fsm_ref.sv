module ve_fsm(input logic clk, rst_n, req, output logic grant);
  typedef enum logic [1:0] {IDLE, SEEN} state_t;
  state_t state_q, state_d;
  always_comb begin
    state_d = state_q;
    grant = 1'b0;
    if (state_q == IDLE && req) state_d = SEEN;
    else if (state_q == SEEN) begin grant = 1'b1; state_d = IDLE; end
  end
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) state_q <= IDLE;
    else state_q <= state_d;
  end
endmodule
