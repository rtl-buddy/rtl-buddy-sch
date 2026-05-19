// Leaf flop module. Real flop logic doesn't matter for this test —
// the reset/domain maps reference the *instance path* of the flop,
// not its implementation.
module ff (
    input  logic clk,
    input  logic rst_n,
    output logic q
);
endmodule
