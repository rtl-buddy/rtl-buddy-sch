// Three instantiation shapes the elaborator can produce:
//   - u_named: full `.port(net)` named connections + named #(.PARAM(...))
//   - u_pos:   positional connections + positional #(8, 16)
//   - u_short: implicit `.port` shorthand (named, no net expression)
// `sub` is intentionally undefined so the children render as
// blackbox leaves; the connection-shape extraction is what we test
// here, not hierarchy resolution.
module top;
    sub  #(.WIDTH(16), .DEPTH(32))  u_named (.clk(clk), .rst_n(rst_n), .q(q));
    sub  #(16, 32)                  u_pos   (clk, rst_n, q);
    sub                             u_short (.clk, .rst_n, .q);
endmodule
