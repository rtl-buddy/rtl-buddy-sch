// prg_csr — register block; keeps its synchronizer child, which the
// module-level `leaf` pragma on prg_sync renders as a single box.
module prg_csr (
    input  logic       clk,
    input  logic       rst_n,
    input  logic [7:0] din,
    output logic [7:0] dout
);

  logic rst_sync;

  prg_sync #(.STAGES(2)) u_sync (
      .clk(clk),
      .d(rst_n),
      .q(rst_sync)
  );

  always_ff @(posedge clk) dout <= din;

endmodule
