// An RTL module deliberately named after a Nangate45 Liberty cell.
//
// The ``instances`` rows of ``phys-model.json`` carry ``module``
// fields in the LIBERTY namespace — the cell each mapped leaf is an
// instance of — and several of them are also spelled ``DFF_X1``. A
// join that matched those names against module names would pile
// every flop's power onto this one node; the overlay's power channel
// joins on instance paths only, so this node's power is exactly what
// sits under ``u_dff``. Its area comes from the ``modules`` row of
// the same name, which IS this RTL module (the correct RTL↔RTL join).
module DFF_X1 (
    input  logic clk,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) q <= d;
endmodule
