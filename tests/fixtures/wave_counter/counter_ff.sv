// Child of counter. The wave overlay should pick up its
// clk/rst_n/q port values from the corresponding signals in the
// VCD's tb.dut.u_ff scope.

module counter_ff (
    input  logic       clk,
    input  logic       rst_n,
    output logic [7:0] q
);
endmodule
