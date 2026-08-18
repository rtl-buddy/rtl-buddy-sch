// blk_producer — non-leaf block: renders as a cluster, so edges that
// touch it must clip at its border via ltail/lhead.
//
// Both outputs are reached only through a continuous assign, so the
// connectivity analyzer has to hop the alias to see them at all.
module blk_producer (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [15:0] cmd,
  output logic        valid,
  output logic [7:0]  payload
);

  logic [7:0] stage;

  blk_leaf u_stage (
    .clk,
    .rst_n,
    .din  (cmd[7:0]),
    .dout (stage)
  );

  assign payload = stage;
  assign valid   = |cmd[15:8];

endmodule
