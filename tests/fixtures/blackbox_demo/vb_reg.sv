// vb_reg — resolved output stage of the blackbox fixture.
module vb_reg (
    input  logic       clk,
    input  logic [7:0] d,
    output logic [7:0] q
);

  always_ff @(posedge clk) q <= d;

endmodule
