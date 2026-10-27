// rtl-buddy-sch#209: conditional generates — decidable from parameters
// (if/else, else-if chain, case) and undecidable ($bits is not
// evaluated, so both arms are kept and marked conditional).
module gi_leaf (input logic clk, input logic [7:0] d, output logic [7:0] q);
  assign q = d;
endmodule

module gen_if #(parameter USE_FAST = 1, parameter MODE = 2) (
  input  logic       clk,
  input  logic [7:0] din,
  output logic [7:0] dout
);
  if (USE_FAST) begin : g_fast
    gi_leaf u_x (.clk(clk), .d(din), .q(dout));
  end else begin : g_slow
    gi_leaf u_y (.clk(clk), .d(din), .q(dout));
  end

  if (MODE == 1) gi_leaf u_m1 (.clk(clk), .d(din), .q());
  else if (MODE == 2) gi_leaf u_m2 (.clk(clk), .d(din), .q());
  else gi_leaf u_m3 (.clk(clk), .d(din), .q());

  case (MODE)
    1: begin : c1 gi_leaf u_c (.clk(clk), .d(din), .q()); end
    2: begin : c2 gi_leaf u_c (.clk(clk), .d(din), .q()); end
    default: gi_leaf u_cd (.clk(clk), .d(din), .q());
  endcase

  if ($bits(din) == 8) begin : g_bits
    gi_leaf u_b (.clk(clk), .d(din), .q());
  end else begin : g_nobits
    gi_leaf u_nb (.clk(clk), .d(din), .q());
  end
endmodule

module gen_if_top (input logic clk, input logic [7:0] din, output logic [7:0] dout);
  gen_if #(.USE_FAST(0), .MODE(1)) u_slow (.clk(clk), .din(din), .dout());
  gen_if u_def (.clk(clk), .din(din), .dout(dout));
endmodule
