interface b_if (input logic b_clk); logic v; endinterface
module src_m (input logic clk, output logic q); assign q = 1'b0; endmodule
module sink_m (input logic clk, input logic d); endmodule
module top7 (input logic s_clk);
  b_if bq [2] (.b_clk(s_clk));
  for (genvar t = 0; t < 2; t++) begin : g
    src_m  u_s (.clk(s_clk), .q(bq[t].v));
    sink_m u_k (.clk(s_clk), .d(bq[t].v));
  end
endmodule
