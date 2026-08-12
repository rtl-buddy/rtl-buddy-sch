// Minimal DUT for the dpi_checker fixture: an adder whose result the
// testbench cross-checks against a DPI-bound C reference model. The
// DUT itself declares no DPI — that is the point: checker DPI lives
// in testbench code, so only a TB-rooted export can see it.
module dpi_dut (
    input  logic [7:0] a,
    input  logic [7:0] b,
    output logic [7:0] sum
);
    assign sum = a + b;
endmodule
