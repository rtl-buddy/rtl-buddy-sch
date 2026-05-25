// Tiny testbench-style wrapper that drives the test_mem_if and binds
// it onto test_module_3. Gives us a two-node hierarchy so the
// json_render / SPA-layer tests can join the interface port across
// the instantiation edge.
module tb_top;
    logic clk;
    logic rst;
    logic z;
    test_mem_if u_if();

    test_module_3 dut (
        .clk(clk),
        .rst(rst),
        .m(u_if.sub),
        .z(z)
    );
endmodule
