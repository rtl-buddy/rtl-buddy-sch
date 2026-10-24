// Fixture for rtl-buddy-sch#194: whole-interface (modport) bindings.
interface bus_if #(parameter int W = 8) (input logic clk);
  logic         v;
  logic [W-1:0] d;
  logic         r;
  modport src (output v, output d, input  r);
  modport snk (input  v, input  d, output r);
  modport mon (input  v, input  d, input  r);
endinterface
