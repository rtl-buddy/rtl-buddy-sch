// Asymmetric FIFO: u_wr_ptr lives in clk_a, u_rd_ptr lives in clk_b
// per the companion clock_map.json. Both pointers share rst_n in
// this fixture — but u_rd_ptr is sampled in clk_b while rst_n is a
// clk_a-asserted port, which the producer flags as an
// async-deassert RDC crossing in reset_map.json.
module fifo (
    input  logic clk_a,
    input  logic clk_b,
    input  logic rst_n
);
    ff u_wr_ptr (.clk(clk_a), .rst_n(rst_n), .q());
    ff u_rd_ptr (.clk(clk_b), .rst_n(rst_n), .q());
endmodule
