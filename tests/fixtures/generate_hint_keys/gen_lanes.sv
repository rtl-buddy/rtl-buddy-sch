// rtl-buddy-sch#217: hint keys for generate-scoped instances and nets.
// One loop with a local net, a conditional arm, a symbolic loop, a
// plain (non-generate) instance as the control, and a placement whose
// override grows the loop past the module's default elaboration.
module ghk_src (output logic o);
  assign o = 1'b0;
endmodule

module ghk_snk (input logic i, output logic o);
  assign o = i;
endmodule

module ghk_lanes #(parameter int N = 2) (output logic [N-1:0] q);
  ghk_src u_plain (.o());
  for (genvar t = 0; t < N; t++) begin : lane
    logic w;
    ghk_src u_src (.o(w));
    ghk_snk u_snk (.i(w), .o(q[t]));
    ghk_src u_tap (.o());  // rbsch: hide
  end : lane
  if (N > 1) begin : wide
    logic x;
    ghk_src u_extra (.o(x));
  end else begin : narrow
    logic z;
    ghk_src u_only (.o(z));
  end
  for (genvar k = 0; k < $bits(q); k++) begin : sym
    logic y;
    ghk_src u_s (.o(y));
  end
endmodule

module ghk_top (output logic [3:0] q, output logic [1:0] r);
  ghk_lanes #(.N(4)) u_four (.q(q));
  ghk_lanes u_two (.q(r));
endmodule
