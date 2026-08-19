// nh_top — phase-2 net-classification fixture (epic #159).
//
// Exercises every net-vocabulary effect end to end:
//   * `tick` is a clock the name regexes cannot see — without its
//     pragma it leaks into the dataflow as edges from the port
//     anchor into both children.
//   * `clk_result` is data whose name reads as a clock — without its
//     pragma the edge to the output port is suppressed.
//   * `stage_q` is the money path (`main`); `done_status` flows the
//     opposite way and is status wiring (`side`).
module nh_top (
    input  logic       clk,
    input  logic       tick,        // rbsch: clock
    input  logic [7:0] din,
    output logic [7:0] clk_result   // rbsch: data
);

  logic [7:0] stage_q;      // rbsch: main
  logic       busy_status;
  logic       done_status;  // rbsch: side

  nh_producer u_prod (
      .clk(clk),
      .tick(tick),
      .din(din),
      .done(done_status),
      .q(stage_q),
      .busy(busy_status)
  );

  nh_consumer u_cons (
      .clk(clk),
      .tick(tick),
      .d(stage_q),
      .busy(busy_status),
      .done(done_status),
      .result(clk_result)
  );

endmodule
