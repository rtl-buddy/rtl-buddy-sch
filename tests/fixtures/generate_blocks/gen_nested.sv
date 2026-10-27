// rtl-buddy-sch#209: a for-in-for array, a loop whose bound does not
// evaluate (one symbolic g[*] iteration), and per-iteration local nets.
module gn_leaf (input logic clk, input logic [7:0] d, output logic [7:0] q);
  assign q = d;
endmodule

module gen_nested #(parameter ROWS = 2, parameter COLS = 3) (
  input  logic       clk,
  input  logic [7:0] din,
  output logic [7:0] dout
);
  localparam int LANES = ROWS - 1;

  for (genvar r = 0; r < ROWS; r++) begin : row
    for (genvar c = 0; c < COLS; c++) begin : col
      gn_leaf u_pe (.clk(clk), .d(din), .q());
    end
  end

  for (genvar k = 0; k < $bits(din); k++) begin : gsym
    gn_leaf u_s (.clk(clk), .d(din), .q());
  end

  for (genvar t = 0; t <= LANES; t++) begin : lane
    logic [7:0] w;
    gn_leaf u_a (.clk(clk), .d(din), .q(w));
    gn_leaf u_b (.clk(clk), .d(w), .q());
  end
endmodule
