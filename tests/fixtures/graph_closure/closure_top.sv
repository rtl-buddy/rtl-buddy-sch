// Closure fixture for the graph export: every link endpoint must
// resolve to an emitted node even when the frontend could not
// recover the declaration the binding names.
module closure_top;
    logic clk;
    logic rst_n;
    int   budget;

    closure_bus_if #(.AW(8)) u_bus (.clk(clk), .rst_n(rst_n));

    closure_leaf #(.AW(8), .DW(32)) u_leaf (
        .clk    (clk),
        .addr   (u_bus.addr),
        .budget (budget),
        .data   ()
    );
endmodule
