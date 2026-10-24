// The issue's repro, verbatim. Expected block-diagram edges:
//   u_prod -> u_cons [shared]     u_prod -> u_mon [shared] (side)
//   u_s0   -> u_s1   [mid]
// and no box for `shared` / `mid`.
module top (input logic clk, bus_if.snk in_bus, bus_if.src out_bus, output logic seen);
  bus_if #(.W(8)) shared (.clk(clk));
  bus_if #(.W(8)) mid    (.clk(clk));
  producer #(.W(8)) u_prod (.clk(clk), .o(shared));
  consumer #(.W(8)) u_cons (.clk(clk), .i(shared));
  monitor           u_mon  (.clk(clk), .m(shared), .seen(seen));
  stage    #(.W(8)) u_s0   (.clk(clk), .i(in_bus), .o(mid));
  stage    #(.W(8)) u_s1   (.clk(clk), .i(mid),    .o(out_bus));
endmodule
