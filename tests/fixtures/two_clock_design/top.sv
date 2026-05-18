// Two-clock top. The hierarchy resolves to
//   top
//   └── u_fifo : fifo
//       ├── u_wr_ptr : ff
//       └── u_rd_ptr : ff
// The companion domain_map.json pins u_wr_ptr to clk_a and u_rd_ptr
// to clk_b, exercising the renderer's predominant-clock aggregation
// (mixed at u_fifo → alphabetical tie-break → clk_a).
module top (
    input  logic clk_a,
    input  logic clk_b
);
    fifo u_fifo (.clk_a(clk_a), .clk_b(clk_b));
endmodule
