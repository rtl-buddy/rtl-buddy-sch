// Phase 8 wave-overlay fixture. Trivial two-flop counter with one
// child module; paired with counter.vcd so the wave overlay can
// sample per-port values at known timestamps.

module counter (
    input  logic       clk,
    input  logic       rst_n,
    output logic [7:0] q
);
    counter_ff u_ff (.clk(clk), .rst_n(rst_n), .q(q));
endmodule
