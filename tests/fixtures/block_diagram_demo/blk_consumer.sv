// blk_consumer — leaf sink block.
module blk_consumer (
  input  logic       clk,
  input  logic       rst_n,
  input  logic       valid,
  input  logic [7:0] payload,
  output logic [7:0] result
);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)     result <= '0;
    else if (valid) result <= payload;
  end

endmodule
