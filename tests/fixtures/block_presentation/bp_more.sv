// rtl-buddy-sch#196: the three presentation controls on one top.
//
// - params=: u_regs shows no parameter rows, bp_fifo keeps only DEPTH.
// - root-port bundle=: the APB port group and each clk/rst pair
//   render as one flag; hide drops the debug enable's flag and edge.
// - clock color=: s_clk is pinned to the palette's blue.

module bp_apb_slave #(parameter int AW = 12, parameter int DW = 32, parameter int DEPTH = 4) (
    input  logic          pclk,
    input  logic          presetn,
    input  logic          psel,
    input  logic          penable,
    input  logic          pwrite,
    input  logic [AW-1:0] paddr,
    input  logic [DW-1:0] pwdata,
    output logic [DW-1:0] prdata,
    output logic          pready
);
endmodule

// rbsch: params=DEPTH
module bp_fifo #(parameter int W = 8, parameter int DEPTH = 4) (
    input  logic         f_clk,
    input  logic [W-1:0] din,
    output logic [W-1:0] dout
);
endmodule

module bp_dbg (input logic en);
endmodule

module bp_top (
    // rbsch: bundle=s_dom
    input  logic        s_clk,    // rbsch: clock color=blue
    input  logic        s_rst_n,

    // rbsch: bundle=f_dom
    input  logic        f_clk,
    input  logic        f_rst_n,

    // rbsch: bundle=apb
    input  logic        psel,
    input  logic        penable,
    input  logic        pwrite,
    input  logic [11:0] paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready,

    input  logic        dbg_en,   // rbsch: hide
    input  logic [7:0]  din,
    output logic [7:0]  dout
);
  bp_apb_slave #(.AW(12), .DW(32), .DEPTH(16)) u_regs (  // rbsch: params=none
      .pclk(s_clk), .presetn(s_rst_n),
      .psel, .penable, .pwrite, .paddr, .pwdata, .prdata, .pready
  );
  bp_fifo #(.W(8), .DEPTH(32)) u_fifo (.f_clk(f_clk), .din(din), .dout(dout));
  bp_dbg u_dbg (.en(dbg_en));
endmodule
