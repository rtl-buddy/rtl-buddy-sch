// Asymmetric FIFO: u_wr_ptr lives in clk_a, u_rd_ptr lives in clk_b
// per the companion domain_map.json. The CDC analyzer would flag the
// pointer-comparison crossings — this fixture exercises the
// rtl-buddy-view consumer side of that overlay only.
module fifo (
    input  logic clk_a,
    input  logic clk_b
);
    ff u_wr_ptr (.clk(clk_a), .q());
    ff u_rd_ptr (.clk(clk_b), .q());
endmodule
