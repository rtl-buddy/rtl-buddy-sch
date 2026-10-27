// rtl-buddy-sch#210 repro, modelled on the template's demo_cdc_open_top:
// a root input registered in clk_a before it is handed to a synchronizer
// in clk_b. The registered path is the only thing connecting flag_a to
// u_flag_sync, and the reset steers it only through an ``if``.
module pf_top (
  input  logic clk_a,
  input  logic clk_b,
  input  logic arst_a_n,
  input  logic arst_b_n,
  input  logic flag_a,
  input  logic en_a,
  output logic flag_b
);
  logic rst_a_n;
  logic rst_b_n;
  pf_reset_sync u_reset_sync_a (.clk(clk_a), .arst_n(arst_a_n), .rst_n(rst_a_n));
  pf_reset_sync u_reset_sync_b (.clk(clk_b), .arst_n(arst_b_n), .rst_n(rst_b_n));

  logic flag_a_q;
  always_ff @(posedge clk_a or negedge rst_a_n)
    if (!rst_a_n) flag_a_q <= 1'b0;
    else          flag_a_q <= flag_a;

  pf_sync u_flag_sync (.clk(clk_b), .rst_n(rst_b_n), .d(flag_a_q), .q(flag_b));

  // A data enable read only as a condition: no flow edge, but it is
  // logic in the body, so --prune-flags must keep its flag.
  logic held;
  always_ff @(posedge clk_a)
    if (en_a) held <= flag_a_q;
  pf_sink u_held (.clk(clk_a), .rst_n(rst_a_n), .d(held));
endmodule
