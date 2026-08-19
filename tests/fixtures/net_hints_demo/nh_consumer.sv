// nh_consumer — sinks the fixture's data + status nets.
module nh_consumer (
    input  logic       clk,
    input  logic       tick,
    input  logic [7:0] d,
    input  logic       busy,
    output logic       done,
    output logic [7:0] result
);

  always_ff @(posedge clk) begin
    if (!busy && tick) result <= d;
    done <= !busy;
  end

endmodule
