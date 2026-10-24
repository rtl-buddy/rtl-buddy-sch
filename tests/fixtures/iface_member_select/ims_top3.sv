// bundle= on the interface instance names every member edge: the
// enables (2 nets, u_csr -> u_peer) and the status return path
// (1 net, u_peer -> u_csr) fold into one cfg_bus edge.
module top3;
  cfg_if cfg ();  // rbsch: bundle=cfg_bus
  csr  u_csr  (.a_en(cfg.a_en), .b_en(cfg.b_en), .c_sta(cfg.c_sta));
  peer u_peer (.a_en(cfg.a_en), .b_en(cfg.b_en), .c_sta(cfg.c_sta));
endmodule
