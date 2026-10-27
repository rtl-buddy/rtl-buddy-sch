// One destination per CDC verdict the overlay distinguishes
// (rtl-buddy-sch#211). The companion domain maps say which is which:
//   u_sync       2ff, findings []           -> recognised-safe
//   u_flagged    2ff, findings [CDC-003]    -> hazard
//   u_raw        no scheme, [CDC-001]       -> hazard
//   u_unchecked  2ff, findings null         -> hazard (rules never ran)
//   u_mix        hazard from clk_a + safe 2ff from clk_c
//   u_dual       safe from both: 2ff+handshake from clk_a, 2ff from clk_c
//   u_bridge     safe both ways (2ff in, gray back) -> two-pair grid
module cdc_top (
    input  logic clk_a,
    input  logic clk_b,
    input  logic clk_c,
    input  logic d_a,
    input  logic d_c,
    output logic q_sync,
    output logic q_flagged,
    output logic q_raw,
    output logic q_unchecked,
    output logic q_mix,
    output logic q_dual,
    output logic a_out,
    output logic b_out
);
    logic a_q;
    logic c_q;
    ff    u_src_a     (.clk(clk_a), .d(d_a), .q(a_q));
    ff    u_src_c     (.clk(clk_c), .d(d_c), .q(c_q));
    sync2 u_sync      (.clk(clk_b), .d(a_q), .q(q_sync));
    sync2 u_flagged   (.clk(clk_b), .d(a_q), .q(q_flagged));
    ff    u_raw       (.clk(clk_b), .d(a_q), .q(q_raw));
    sync2 u_unchecked (.clk(clk_b), .d(a_q), .q(q_unchecked));
    mix2  u_mix       (.clk(clk_b), .d0(a_q), .d1(c_q), .q(q_mix));
    mix2  u_dual      (.clk(clk_b), .d0(a_q), .d1(c_q), .q(q_dual));
    bridge u_bridge (
        .clk_a(clk_a), .clk_b(clk_b),
        .a_in(a_q), .b_in(q_sync),
        .a_out(a_out), .b_out(b_out)
    );
endmodule
