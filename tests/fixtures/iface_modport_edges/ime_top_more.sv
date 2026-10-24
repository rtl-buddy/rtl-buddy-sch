// A modport-less port faces the modport pins it meets:
//   u_p -> u_t1 [a]   (the tap reads a source)
//   u_t2 -> u_c [b]   (the tap feeds a sink)
module top_generic (input logic clk);
  bus_if a (.clk(clk));
  bus_if b (.clk(clk));
  producer u_p  (.clk(clk), .o(a));
  tap      u_t1 (.b(a));
  tap      u_t2 (.b(b));
  consumer u_c  (.clk(clk), .i(b));
endmodule

// A whole binding meets a member select one member at a time, in the
// modport's per-signal direction: u_r drives bus.r, which the src
// modport reads, so u_r -> u_p [bus.r]. A modport select (`bus.src`)
// binds the whole interface.
module top_member (input logic clk);
  bus_if bus (.clk(clk));
  producer  u_p (.clk(clk), .o(bus.src));
  ready_src u_r (.rdy(bus.r));
endmodule
