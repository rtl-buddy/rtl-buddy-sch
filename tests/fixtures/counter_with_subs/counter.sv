// Top-level counter with two child instances. ``counter_ff`` is
// defined in counter_ff.sv (resolved hierarchy); ``sub_x`` is
// deliberately not defined anywhere in the filelist so the graph
// builder treats it as a blackbox leaf.
module counter (
    input  logic clk,
    input  logic rst_n,
    output logic q
);
    counter_ff u_ff (.clk(clk), .q(q));
    sub_x      u_x  (.clk(clk));
endmodule
