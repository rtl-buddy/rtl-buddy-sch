// bd_consumer — command sink side of the cmd_bus handshake.
module bd_consumer (
    input  logic        clk,
    input  logic        valid,
    input  logic [15:0] data,
    output logic        ready,
    output logic [7:0]  result
);

  always_ff @(posedge clk) begin
    ready <= 1'b1;
    if (valid && ready) result <= data[7:0];
  end

endmodule
