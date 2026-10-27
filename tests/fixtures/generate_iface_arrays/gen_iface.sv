// rtl-buddy-sch#218: generate-local interfaces beyond the issue's repro.
module snk_m (bus_if.dst dst); endmodule
module bit_m (input logic a, output logic y); assign y = a; endmodule
module one_m (bus_if.dst sub, output logic q); assign q = sub.v; endmodule
module drv_m (output logic o); assign o = 1'b0; endmodule
module rd_m (input logic i, output logic q); assign q = i; endmodule

// A plain logic net declared inside the generate block (control).
module gen_logic (output logic [1:0] q);
  for (genvar t = 0; t < 2; t++) begin : g
    logic w;
    drv_m i_drv (.o(w));
    rd_m  i_rd  (.i(w), .q(q[t]));
  end
endmodule

// A scalar interface per iteration: the iterations share nothing.
module gen_scalar (output logic [1:0] q);
  for (genvar t = 0; t < 2; t++) begin : g
    bus_if sub ();
    one_m i_mrg (.sub(sub), .q(q[t]));
    lpb_m i_lpb (.src(sub));
  end
endmodule

// Element bindings and a member select, at module scope and per
// iteration: each iteration of gen_members must draw what mod_members
// draws.
module mod_members (output logic q, output logic y);
  bus_if sub [0:1] ();
  lpb_m i_l0 (.src(sub[0]));
  lpb_m i_l1 (.src(sub[1]));
  snk_m i_s1 (.dst(sub[1]));
  bit_m i_b (.a(sub[1].v), .y(y));
  mrg_m i_mrg (.sub(sub), .q(q));
endmodule
module gen_members (output logic [1:0] q, output logic [1:0] y);
  for (genvar t = 0; t < 2; t++) begin : g
    bus_if sub [0:1] ();
    lpb_m i_l0 (.src(sub[0]));
    lpb_m i_l1 (.src(sub[1]));
    snk_m i_s1 (.dst(sub[1]));
    bit_m i_b (.a(sub[1].v), .y(y[t]));
    mrg_m i_mrg (.sub(sub), .q(q[t]));
  end
endmodule

// An unresolvable bound: one symbolic g[*] iteration.
module gen_sym (output logic q);
  for (genvar t = 0; t < $bits(q); t++) begin : g
    bus_if sub [0:1] ();
    mrg_m i_mrg (.sub(sub), .q(q));
    lpb_m i_lpb (.src(sub[1]));
  end
endmodule

// A conditional arm, and a nested loop binding its parent's interface.
module gen_cond #(parameter EN = 1) (output logic q);
  if (EN) begin : g
    bus_if sub [0:1] ();
    mrg_m i_mrg (.sub(sub), .q(q));
    lpb_m i_lpb (.src(sub[1]));
  end
endmodule
module gen_nest (output logic [1:0] q);
  for (genvar a = 0; a < 2; a++) begin : o
    bus_if sub [0:1] ();
    for (genvar b = 0; b < 2; b++) begin : i
      lpb_m i_lpb (.src(sub[b]));
    end
    mrg_m i_mrg (.sub(sub), .q(q[a]));
  end
endmodule
