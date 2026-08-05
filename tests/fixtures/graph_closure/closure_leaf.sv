// Multi-declarator parameter header: Verible records `AW, DW` as one
// `<unknown>` parameter, so named overrides `.AW(...)` / `.DW(...)`
// have nothing declared to point at. Also carries a non-scalar
// (`int`) port so the width rule has something to stay quiet about.
module closure_leaf #(parameter AW = 8, DW = 32) (
    input  logic          clk,
    input  logic [AW-1:0] addr,
    input  int            budget,
    output logic [DW-1:0] data
);
    assign data = {DW{1'b0}};
endmodule
