// Two clocks, two distinct resets, one async-deassert RDC.
//
// Hierarchy:
//   top
//   ├── u_a : ff    [clk_a, rst_a_n↓]
//   └── u_b : ff    [clk_b, rst_b_n↓]  ⚠RDC[rst_a_n:async-deassert]
//
// u_b's clock is clk_b, but in this scenario it's reset by rst_a_n
// (a reset asserted in clk_a's domain) — that's the textbook
// async-deassert RDC shape. The fixture's companion reset_map.json
// emits exactly that crossing.
module top (
    input  logic clk_a,
    input  logic clk_b,
    input  logic rst_a_n,
    input  logic rst_b_n
);
    ff u_a (.clk(clk_a), .rst_n(rst_a_n), .q());
    ff u_b (.clk(clk_b), .rst_n(rst_a_n), .q());  // RDC: rst_a_n sampled by clk_b
    // u_b nominally has rst_b_n available too — the crossing exists
    // because the design *doesn't* use it. Renderers don't care; they
    // visualize what the producer flagged.
endmodule
