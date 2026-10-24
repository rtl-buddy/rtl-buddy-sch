// Regression guard: a struct-typed variable is a net, not an
// interface, so its field selects keep reducing to the root `s`.
module top4;
  typedef struct packed {
    logic a;
    logic b;
  } pair_t;
  pair_t s;
  pair_src u_src (.p(s));
  blk_in   u_x   (.en(s.a));
  blk_in   u_y   (.en(s.b));
endmodule
