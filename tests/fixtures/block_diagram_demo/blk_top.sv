// blk_top — block-diagram fixture.
//
// Exercises every path the connectivity analyzer has to walk:
//   * implicit `.clk` / `.rst_n` shorthand (no net expression at all)
//   * a continuous-assign alias hop (prod_payload -> prod_payload_q)
//   * a part-select on a pin actual (prod_payload_q[7:0])
//   * a top input port feeding a child, and a child feeding a top
//     output port
//   * a non-leaf child (u_prod), so cluster clipping is covered
module blk_top (
  input  logic        clk,
  input  logic        rst_n,
  input  logic [15:0] cmd_in,
  output logic [7:0]  result_out
);

  logic       prod_valid;
  logic [7:0] prod_payload;
  logic [7:0] prod_payload_q;

  blk_producer u_prod (
    .clk,
    .rst_n,
    .cmd     (cmd_in),
    .valid   (prod_valid),
    .payload (prod_payload)
  );

  assign prod_payload_q = prod_payload;

  blk_consumer u_cons (
    .clk,
    .rst_n,
    .valid   (prod_valid),
    .payload (prod_payload_q[7:0]),
    .result  (result_out)
  );

endmodule
