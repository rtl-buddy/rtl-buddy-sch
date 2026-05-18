// Leaf flop module. Real flop logic doesn't matter for this test —
// the domain map references the *instance path* of the flop, not its
// implementation.
module ff (
    input  logic clk,
    output logic q
);
endmodule
