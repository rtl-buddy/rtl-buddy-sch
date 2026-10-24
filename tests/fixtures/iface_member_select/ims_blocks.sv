module csr (
    output logic a_en,
    output logic b_en,
    input  logic c_sta
);
  assign a_en = c_sta;
  assign b_en = ~c_sta;
endmodule

module blk_in (input logic en);
endmodule

module blk_out (output logic sta);
  assign sta = 1'b0;
endmodule

// The CSR's counterpart: reads both enables, returns the status.
module peer (
    input  logic a_en,
    input  logic b_en,
    output logic c_sta
);
  assign c_sta = a_en & b_en;
endmodule

module pair_src (output logic [1:0] p);
  assign p = 2'b01;
endmodule
