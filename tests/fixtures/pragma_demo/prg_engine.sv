// prg_engine — two pipeline stages; collapsed at its instantiation
// rather than at the module, so a second instantiation elsewhere
// would still expand.
module prg_engine (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] din,
    output logic [7:0] dout
);

  logic [7:0] mid;

  prg_stage u_stage0 (
      .clk(clk),
      .din(din),
      .dout(mid)
  );

  prg_stage u_stage1 (
      .clk(clk),
      .din(mid),
      .dout(dout)
  );

endmodule
