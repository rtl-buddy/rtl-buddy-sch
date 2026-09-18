// Mid-level scope: one submodule plus a gate of its own. The gate
// is what makes ``phys_sub``'s self area non-zero, so the
// self-vs-subtree channel has something to show here.
module phys_sub (
    input  logic clk,
    input  logic d,
    output logic q
);
    logic w;
    phys_leaf u_leaf (.clk(clk), .d(d), .q(w));
    assign q = w & d;
endmodule
