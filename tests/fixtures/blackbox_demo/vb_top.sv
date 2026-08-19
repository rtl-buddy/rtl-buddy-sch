// vb_top — phase-5 blackbox fixture (epic #159).
//
// `vendor_rom` has no source in the filelist, so its pin directions
// are unknowable: without the pragma the `rom_q` group has no known
// driver and the edge into `u_reg` is dropped ("better an
// under-drawn edge than a fabricated direction"). The `in=`/`out=`
// hint recovers it.
module vb_top (
    input  logic       clk,
    input  logic [3:0] addr_in,
    output logic [7:0] data_out
);

  logic [7:0] rom_q;

  vendor_rom u_rom (  // rbsch: in=addr out=q
      .addr(addr_in),
      .q(rom_q)
  );

  vb_reg u_reg (
      .clk(clk),
      .d(rom_q),
      .q(data_out)
  );

endmodule
