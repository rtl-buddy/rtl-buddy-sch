// ld_stage — one pipeline register, reused by every layout_demo stage.
module ld_stage (
    input  logic       clk,
    input  logic [7:0] d,
    output logic [7:0] q
);

  always_ff @(posedge clk) q <= d;

endmodule
