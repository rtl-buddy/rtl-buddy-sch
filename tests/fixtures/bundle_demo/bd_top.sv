// bd_top — phase-3 bundle fixture (epic #159).
//
// `cmd_valid` + `cmd_data` flow forward, `cmd_ready` flows back;
// one `bundle=cmd_bus` pragma above the contiguous run names all
// three, so the diagram draws a single thick `cmd_bus` edge in the
// data direction. `plain_result` stays an ordinary labeled net.
module bd_top (
    input  logic        clk,
    input  logic [15:0] cmd_in,
    output logic [7:0]  result_out
);

  // rbsch: bundle=cmd_bus
  logic        cmd_valid;
  logic [15:0] cmd_data;
  logic        cmd_ready;

  logic [7:0] plain_result;

  bd_producer u_prod (
      .clk(clk),
      .cmd(cmd_in),
      .valid(cmd_valid),
      .data(cmd_data),
      .ready(cmd_ready)
  );

  bd_consumer u_cons (
      .clk(clk),
      .valid(cmd_valid),
      .data(cmd_data),
      .ready(cmd_ready),
      .result(plain_result)
  );

  assign result_out = plain_result;

endmodule
