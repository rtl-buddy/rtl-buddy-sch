// Top of the physical-overlay fixture. Two submodule instances plus
// one gate of its own, matching the ``modules`` rows in
// ``phys-model.json``.
module phys_top (
    input  logic clk,
    input  logic d,
    output logic q
);
    logic mid;
    logic dff_q;
    phys_sub u_sub (.clk(clk), .d(d), .q(mid));
    DFF_X1   u_dff (.clk(clk), .d(mid), .q(dff_q));
    assign q = dff_q & d;
endmodule
