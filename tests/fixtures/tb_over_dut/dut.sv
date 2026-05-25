// Minimal DUT used by the tb_over_dut fixture. Two leaves let the
// renderer exercise multiple children under the DUT subtree when
// the testbench instantiates it.
module dut (
    input  logic clk,
    input  logic d,
    output logic q
);
    logic mid;
    leaf u_a (.clk(clk), .d(d),   .q(mid));
    leaf u_b (.clk(clk), .d(mid), .q(q));
endmodule
