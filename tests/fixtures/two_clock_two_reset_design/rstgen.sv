// Two-flop async-assert / sync-deassert reset synchronizer body.
// The synchronizer's sync-stage cell is exposed as an ``ff`` instance
// named ``u_sync`` so the hierarchy carries an explicit node at
// ``top.u_rstgen.u_sync``; the companion reset_map.json then flags
// that node as a member of the reset-synchronizer set so renderers
// display the ✓rstsync marker.
module rstsync (
    input  logic clk,
    input  logic rst_n,
    output logic rst_sync
);
    ff u_sync (.clk(clk), .rst_n(rst_n), .q(rst_sync));
endmodule
