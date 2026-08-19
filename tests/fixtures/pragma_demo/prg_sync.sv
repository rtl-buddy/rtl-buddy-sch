// prg_sync — a synchronizer whose internals are never the story.
// The standalone module-level pragma below applies to every
// instance of it, wherever it appears.

// rbsch: leaf
module prg_sync #(
    parameter int STAGES = 2
) (
    input  logic clk,
    input  logic d,
    output logic q
);

  prg_ff u_ff0 (
      .clk(clk),
      .d(d),
      .q(q)
  );

endmodule
