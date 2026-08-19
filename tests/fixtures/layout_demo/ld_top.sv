// ld_top — phase-4 layout fixture (epic #159).
//
// Three identical stages, so complexity sorting alone cannot order
// them: `rank=` pins the pipeline order and `group=` draws a dashed
// virtual container around the two front-end stages.
module ld_top (
    input  logic       clk,
    input  logic [7:0] din,
    output logic [7:0] dout
);

  logic [7:0] s1;
  logic [7:0] s2;

  ld_stage u_fetch (  // rbsch: rank=1 group=frontend
      .clk(clk),
      .d(din),
      .q(s1)
  );

  ld_stage u_decode (  // rbsch: rank=2 group=frontend
      .clk(clk),
      .d(s1),
      .q(s2)
  );

  ld_stage u_execute (  // rbsch: rank=3
      .clk(clk),
      .d(s2),
      .q(dout)
  );

endmodule
