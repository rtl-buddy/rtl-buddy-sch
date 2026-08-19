// bd_producer — command source side of the cmd_bus handshake.
module bd_producer (
    input  logic        clk,
    input  logic [15:0] cmd,
    output logic        valid,
    output logic [15:0] data,
    input  logic        ready
);

  always_ff @(posedge clk) begin
    if (ready) begin
      data  <= cmd;
      valid <= |cmd;
    end
  end

endmodule
