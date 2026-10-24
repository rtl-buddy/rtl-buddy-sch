module gen_m (bus_if b); endmodule

// A generic root port bound to a src child is an output flag; one bound
// to a src and a snk child, or to nothing that has a modport, is a
// bidirectional flag — declared, never a stray box.
module top_gen (bus_if g_out, bus_if g_both, bus_if g_none, bus_if g_free);
  src_m  u_s  (.o(g_out));
  src_m  u_s2 (.o(g_both));
  sink_m u_k2 (.i(g_both));
  gen_m  u_g  (.b(g_none));
endmodule
