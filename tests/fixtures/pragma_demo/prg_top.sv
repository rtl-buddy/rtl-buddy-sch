// prg_top — fixture exercising every phase-1 rbsch pragma (epic
// #159): a standalone label, a trailing collapse + label on one
// line, and a standalone hide.
module prg_top (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] din,
    output logic [7:0] dout
);

  logic [7:0] staged;

  // rbsch: label="CSR block"
  prg_csr u_csr (
      .clk(clk),
      .rst_n(rst_n),
      .din(din),
      .dout(staged)
  );

  prg_engine u_engine (  // rbsch: collapse label="ALU datapath"
      .clk(clk),
      .rst_n(rst_n),
      .din(staged),
      .dout(dout)
  );

  // rbsch: hide
  prg_tieoff u_tieoff (
      .clk(clk)
  );

endmodule
