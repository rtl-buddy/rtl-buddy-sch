// Fixture for rtl-buddy-sch#195: interface ports on the diagram root.
// The issue's repro, verbatim. Expected block-diagram flags/edges:
//   "_in_gen" (gen binds a snk, so it feeds u_k)  _in_gen -> u_k
//   "_out_out_bus" on the sink rank               u_s -> _out_out_bus
interface bus_if;
  logic v; logic d; logic r;
  modport src (output v, output d, input r);
  modport snk (input v, input d, output r);
endinterface
module sink_m (bus_if.snk i); assign i.r = 1'b1; endmodule
module src_m  (bus_if.src o); assign o.v = 1'b1; assign o.d = 1'b0; endmodule
// generic (modport-less) interface port `gen`, and a src-modport root port `out_bus`
module top3 (bus_if gen, bus_if.src out_bus);
  sink_m u_k (.i(gen));
  src_m  u_s (.o(out_bus));
endmodule
