// Wrapper top: a testbench around the synthesised design. Rendering
// with ``--tb-top tb_phys_top`` puts a scope ABOVE the model's own
// top, which is the case the overlay's anchor search exists for —
// every rootless model row has to be rooted at ``tb_phys_top.u_dut``
// rather than at the rendered root.
module tb_phys_top;
    logic clk;
    logic d;
    logic q;
    phys_top u_dut (.clk(clk), .d(d), .q(q));
endmodule
