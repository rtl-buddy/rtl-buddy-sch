module producer #(parameter int W = 8) (input logic clk, bus_if.src o);
  assign o.v = 1'b1;
  assign o.d = '0;
endmodule

module consumer #(parameter int W = 8) (input logic clk, bus_if.snk i);
  assign i.r = 1'b1;
endmodule

module monitor (input logic clk, bus_if.mon m, output logic seen);
  assign seen = m.v & m.r;
endmodule

module stage #(parameter int W = 8, parameter int DEPTH = 4, parameter int LAT = 2)
  (input logic clk, bus_if.snk i, bus_if.src o);
  assign o.v = i.v;
  assign o.d = i.d;
  assign i.r = o.r;
endmodule

// A modport-less interface port: it takes whichever direction the
// modport pins it meets leave open.
module tap (bus_if b);
endmodule

// Drives the ready member of a bus by member select.
module ready_src (output logic rdy);
  assign rdy = 1'b1;
endmodule
