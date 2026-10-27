// rtl-buddy-sch#210: leaf blocks for the registered-input repro.
module pf_reset_sync (input logic clk, input logic arst_n, output logic rst_n);
  logic [1:0] chain;
  always_ff @(posedge clk or negedge arst_n)
    if (!arst_n) chain <= '0;
    else         chain <= {chain[0], 1'b1};
  assign rst_n = chain[1];
endmodule

module pf_sync (input logic clk, input logic rst_n, input logic d, output logic q);
  logic [1:0] chain;
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) chain <= '0;
    else        chain <= {chain[0], d};
  assign q = chain[1];
endmodule

module pf_sink (input logic clk, input logic rst_n, input logic d);
endmodule
