// prg_tieoff — debug tap; noise in a documentation figure.
module prg_tieoff (
    input logic clk
);

  logic unused;
  always_ff @(posedge clk) unused <= 1'b0;

endmodule
