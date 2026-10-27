// Leaf cells for the cdc_sync_design fixture (rtl-buddy-sch#211).
module ff (
    input  logic clk,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) q <= d;
endmodule

module sync2 (
    input  logic clk,
    input  logic d,
    output logic q
);
    logic meta;
    always_ff @(posedge clk) begin
        meta <= d;
        q    <= meta;
    end
endmodule

module mix2 (
    input  logic clk,
    input  logic d0,
    input  logic d1,
    output logic q
);
    always_ff @(posedge clk) q <= d0 ^ d1;
endmodule

module bridge (
    input  logic clk_a,
    input  logic clk_b,
    input  logic a_in,
    input  logic b_in,
    output logic a_out,
    output logic b_out
);
    sync2 u_ab (.clk(clk_b), .d(a_in), .q(b_out));
    sync2 u_ba (.clk(clk_a), .d(b_in), .q(a_out));
endmodule
