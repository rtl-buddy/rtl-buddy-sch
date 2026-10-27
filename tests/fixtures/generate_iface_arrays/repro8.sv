// rtl-buddy-sch#218: an interface array declared inside a generate
// block, bound whole to one sibling and by element to another. The
// issue's repro (top8) verbatim, plus its module-scope control (top9).
interface bus_if; logic v; logic d; modport src(output v, d); modport dst(input v, d); endinterface
module mrg_m #(parameter N=2) (bus_if.dst sub [0:N-1], output logic q); assign q = sub[0].v; endmodule
module lpb_m (bus_if.src src); assign src.v = 1'b0; assign src.d = 1'b0; endmodule
module top8 (output logic [1:0] q);
  for (genvar t = 0; t < 2; t++) begin : g
    bus_if sub [0:1] ();
    mrg_m i_mrg (.sub(sub), .q(q[t]));
    lpb_m i_lpb (.src(sub[1]));
  end : g
endmodule
module top9 (output logic q); bus_if sub [0:1] (); mrg_m i_mrg (.sub(sub), .q(q)); lpb_m i_lpb (.src(sub[1])); endmodule
