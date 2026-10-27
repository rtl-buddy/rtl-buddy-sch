// rtl-buddy-sch#208 repro, verbatim: an enum constant used as an
// index in a reset select AND a data select.
module sink_m (input logic clk, input logic rst, input logic d); endmodule
module src_m  (input logic clk, output logic q); assign q = 1'b0; endmodule
// An enum constant used as an index in a reset select AND a data select.
module top6 (input logic s_clk, input logic [1:0] s_rst_l);
  typedef enum int {SR_A, SR_B} sr_e;
  logic [1:0] dv;
  src_m  u_s0 (.clk(s_clk), .q(dv[SR_A]));
  src_m  u_s1 (.clk(s_clk), .q(dv[SR_B]));
  sink_m u_k0 (.clk(s_clk), .rst(s_rst_l[SR_A]), .d(dv[SR_A]));
  sink_m u_k1 (.clk(s_clk), .rst(s_rst_l[SR_B]), .d(dv[SR_B]));
endmodule
