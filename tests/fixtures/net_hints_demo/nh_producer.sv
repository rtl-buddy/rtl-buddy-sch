// nh_producer — drives the fixture's data + status nets.
module nh_producer (
    input  logic       clk,
    input  logic       tick,
    input  logic [7:0] din,
    input  logic       done,
    output logic [7:0] q,
    output logic       busy
);

  always_ff @(posedge clk) begin
    q    <= din;
    busy <= tick & ~done;
  end

endmodule
