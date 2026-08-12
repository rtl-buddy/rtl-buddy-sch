// Testbench binding a C golden model over DPI (#127). Exercises the
// three declaration shapes the extractor must tell apart:
//   - plain import          -> c_symbol == sv name
//   - aliased import        -> c_symbol from the `c_name =` alias
//   - export                -> SV implements, C calls
// plus a call site of the plain import, for the `calls` edge.
module tb_dpi;
    import "DPI-C" function int add_ref(input int a, input int b);
    import "DPI-C" golden_scale = function int scale_ref(input int x);
    export "DPI-C" function sv_report_mismatch;

    logic [7:0] a;
    logic [7:0] b;
    logic [7:0] sum;
    int         expected;

    dpi_dut u_dut (.a(a), .b(b), .sum(sum));

    function void sv_report_mismatch();
    endfunction

    initial begin
        a = 8'd3;
        b = 8'd4;
        expected = add_ref(int'(a), int'(b));
    end
endmodule
