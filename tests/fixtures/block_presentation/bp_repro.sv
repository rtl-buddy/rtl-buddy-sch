// rtl-buddy-sch#196 repro, verbatim: an integration-style top whose
// child box carries W / DEPTH parameter rows, and whose clk flag has
// no drawable edge (the classifier drops the clock group).
module child #(parameter int W = 8, parameter int DEPTH = 4) (input logic clk, input logic [W-1:0] d);
endmodule
module top (input logic clk, input logic [7:0] d);
  child #(.W(8), .DEPTH(16)) u_c (.clk(clk), .d(d));   // box shows W/DEPTH rows; clk flag has no edge
endmodule
