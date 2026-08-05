// Interface with *header ports* — the shape that used to leave the
// graph export's `connects` edges dangling (rtl-buddy-view#126).
// `extractor.Interface` models signals / parameters / modports but
// not the interface's own port list, so `.clk(...)` / `.rst_n(...)`
// at the instantiation site names formals no declaration supplies.
interface closure_bus_if #(
    parameter AW = 8
) (
    input logic clk,
    input logic rst_n
);
    logic [AW-1:0] addr;
    logic          valid;

    modport sub(input addr, input valid);
endinterface
