// Two-clock, single-reset top with one RDC crossing.
// Hierarchy:
//   top
//   ├── u_rstgen : rstsync   (recognized sync; ✓rstsync badge)
//   └── u_fifo   : fifo
//       ├── u_wr_ptr : ff    [clk_a, rst_n↓]
//       └── u_rd_ptr : ff    [clk_b, rst_n↓]  ⚠RDC[rst_n:async-deassert]
//
// The async-deassert flag on u_rd_ptr exercises the renderer's
// RDC-crossing suffix path; u_rstgen exercises the synchronizer-set
// marker path.
module top (
    input  logic clk_a,
    input  logic clk_b,
    input  logic rst_n
);
    logic rst_sync;
    rstsync u_rstgen (.clk(clk_b), .rst_n(rst_n), .rst_sync(rst_sync));
    fifo u_fifo (.clk_a(clk_a), .clk_b(clk_b), .rst_n(rst_n));
endmodule
