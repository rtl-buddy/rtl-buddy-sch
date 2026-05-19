// Two-flop async-assert / sync-deassert reset synchroniser chain.
//
// Hierarchy:
//   top
//   ├── u_sync_stage1 : ff   (in reset_synchronizers set — head)
//   ├── u_sync_stage2 : ff   (in reset_synchronizers set — tail)
//   └── u_data       : ff    [clk_dst, rst_sync↓]
//
// The companion reset_map.json marks both ``u_sync_stage*`` flops as
// members of the reset-synchroniser set so renderers display the
// ✓rstsync marker on each, and shows ``u_data`` reset by the
// synthesised ``rst_sync`` (the synchroniser's output). No RDC
// crossing in this fixture — the synchroniser is the *fix*, not a
// bug — so it exercises the "vetted sync, no warning" rendering
// path explicitly.
module top (
    input  logic clk_dst,
    input  logic rst_a_n,
    output logic q
);
    logic stage1_q;
    logic rst_sync;
    ff u_sync_stage1 (.clk(clk_dst), .d(1'b1), .q(stage1_q));
    ff u_sync_stage2 (.clk(clk_dst), .d(stage1_q), .q(rst_sync));
    ff u_data        (.clk(clk_dst), .d(1'b0), .q(q));
endmodule
