// Fixture for rtl-buddy-sch#193: scalar ports bound to interface
// *members* must key on the member path, not the interface name.
interface cfg_if;
  logic a_en;   // written by u_csr, read by u_a only
  logic b_en;   // written by u_csr, read by u_b only
  logic c_sta;  // written by u_c, read by u_csr only
endinterface
