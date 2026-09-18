// Leaf of the physical-overlay fixture. One flop, no instances of
// its own — so every ``instances`` row under ``u_leaf`` in
// ``phys-model.json`` rolls up into this node and nowhere deeper.
module phys_leaf (
    input  logic clk,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) q <= d;
endmodule
