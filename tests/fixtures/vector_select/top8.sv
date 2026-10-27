// rtl-buddy-sch#208: whole-vector references next to element selects.
module src2_m  (input logic clk, output logic [1:0] q); assign q = 2'b0; endmodule
module sink2_m (input logic clk, input logic [1:0] d); endmodule
module top8 (input logic s_clk, input logic sel, output logic [1:0] o);
  localparam int LO = 0;
  logic [1:0] bus;
  logic [1:0] dv;
  logic       x;
  // A whole driver reaches an element reader and a whole reader.
  src2_m  u_w  (.clk(s_clk), .q(bus));
  sink_m  u_e0 (.clk(s_clk), .rst(1'b0), .d(bus[LO]));
  sink2_m u_wb (.clk(s_clk), .d(bus));
  // Element drivers (one through an assign) reach a whole reader,
  // and a non-constant select, which falls back to the vector.
  src_m   u_x  (.clk(s_clk), .q(x));
  assign dv[0] = x;
  src_m   u_e1 (.clk(s_clk), .q(dv[1]));
  sink2_m u_wd (.clk(s_clk), .d(dv));
  sink_m  u_v  (.clk(s_clk), .rst(1'b0), .d(dv[sel]));
  assign o = bus;
endmodule
