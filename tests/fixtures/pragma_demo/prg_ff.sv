// prg_ff — leaf flop, only reachable through prg_sync.
module prg_ff (
    input  logic clk,
    input  logic d,
    output logic q
);

  always_ff @(posedge clk) q <= d;

endmodule
