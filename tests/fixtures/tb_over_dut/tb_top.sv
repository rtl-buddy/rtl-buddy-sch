// Testbench top instantiating the DUT plus a procedural clock/reset
// generator and a stimulus driver — the kind of scope the DUT-rooted
// view currently hides. Exercised by the --tb-top CLI mode + the
// SPA's dut-anchors derivation: filtering nodes[] for
// module == design.dut_top recovers the single DUT instance at
// `tb_top.u_dut`.
module clkgen (output logic clk);
endmodule

module driver (input logic clk, output logic d);
endmodule

module tb_top;
    logic clk;
    logic d;
    logic q;

    clkgen u_clkgen (.clk(clk));
    driver u_driver (.clk(clk), .d(d));
    dut    u_dut    (.clk(clk), .d(d), .q(q));
endmodule
