// SystemVerilog interface fixture for interface-port extraction tests
// (rtl-buddy/rtl-buddy-view#102). A minimal two-signal "memory request"
// bundle with two modports (``master`` / ``sub``).
interface test_mem_if;
    logic        req;
    logic [7:0]  addr;

    modport master(output req, output addr);
    modport sub(input req, input addr);
endinterface
