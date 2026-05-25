// Producer module with a wire port AND an interface port — exercises
// the kInterfacePortHeader CST signature from #102. The module has a
// flat scalar port (``clk``) and an interface port (``m``) so the
// extractor needs to handle both kinds inside the same port list.
module test_module_3 (
    input  logic       clk,
    input  logic       rst,
    test_mem_if.sub    m,
    output logic       z
);
endmodule
