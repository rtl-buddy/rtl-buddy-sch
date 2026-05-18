// Parameterized FIFO top with two child instances. Exercises:
//   - module-level parameter declarations with default values
//   - named parameter overrides on instances (#(.WIDTH(...), .DEPTH(...)))
//   - mixed overrides: the .ptr instance overrides only WIDTH and
//     leaves DEPTH at the child's default.
// `fifo_core` and `fifo_ptr` are not defined in the filelist, so both
// surface as blackbox leaves in the rendered hierarchy.
module fifo #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 16
)(
    input  logic clk,
    input  logic rst_n
);
    fifo_core #(.WIDTH(WIDTH), .DEPTH(DEPTH)) u_core (.clk(clk));
    fifo_ptr  #(.WIDTH(16))                   u_ptr  (.clk(clk));
endmodule
