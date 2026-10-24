// The issue's repro: members of one local interface instance, plus
// two elements of an interface array. Expected edges:
//   u_csr -> u_a  (cfg.a_en)     u_csr -> u_b  (cfg.b_en)
//   u_c   -> u_csr (cfg.c_sta)
//   u_p0  -> u_k0 (q[0].c_sta)   u_p1  -> u_k1 (q[1].c_sta)
module top2;
  cfg_if cfg ();
  csr     u_csr (.a_en(cfg.a_en), .b_en(cfg.b_en), .c_sta(cfg.c_sta));
  blk_in  u_a   (.en(cfg.a_en));
  blk_in  u_b   (.en(cfg.b_en));
  blk_out u_c   (.sta(cfg.c_sta));

  cfg_if q [2] ();
  blk_out u_p0 (.sta(q[0].c_sta));
  blk_out u_p1 (.sta(q[1].c_sta));
  blk_in  u_k0 (.en(q[0].c_sta));
  blk_in  u_k1 (.en(q[1].c_sta));
endmodule
