// prg_stage — pipeline stage inside prg_engine.
module prg_stage (
    input  logic       clk,
    input  logic [7:0] din,
    output logic [7:0] dout
);

  always_ff @(posedge clk) dout <= din;

endmodule
