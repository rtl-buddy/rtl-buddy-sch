"""Phase 0 of the axi-perf ↔ tb-top unification (rtl-buddy-view).

A verible-interface bundle (one carrying ``interface_instance``) attaches
to the interface-port *pin* on its endpoint node as a ``bundle_pins``
entry under ``node.overlays["axi-perf"]`` — the "tb-top mechanism" attach
point — instead of (or in addition to) the legacy (master, slave) edge
match. This is what lets the SPA decorate the existing ▶▶ interface pin
with perf stats rather than drawing a parallel master↔slave edge.

The legacy edge / interconnect paths are kept intact and covered by
``test_axi_perf_render_json.py``; here we cover the new node-pin path and
its graceful degradation for regex bundles (no ``interface_instance``).
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from rtl_buddy_view.axi_perf_annotations import load_axi_perf_map
from rtl_buddy_view.extractor import (
    Instance,
    Module,
    ModuleTable,
    Port,
    PortConnection,
    SourceLocation,
)
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import json_render


def _loc(file: str, line: int = 1) -> SourceLocation:
    return SourceLocation(
        file=file, start_line=line, start_column=1, end_line=line + 1, end_column=1
    )


def _bundle_payload(
    *,
    name: str = "tb_to_dut",
    master: str,
    slave: str,
    interface_instance: str | None = None,
    slave_modport: str | None = None,
) -> dict:
    payload = {
        "name": name,
        "master_path": master,
        "slave_path": slave,
        "protocol": "AXI4",
        "data_width": 64,
        "id_width": 4,
        "default_view": "parent",
        "channels": {
            "ar": {"util_pct": 25.0, "bp_pct": 2.0, "peak_occ": 8, "txns": 30000},
            "aw": {"util_pct": 15.0, "bp_pct": 1.0, "peak_occ": 4, "txns": 18000},
            "r": {"util_pct": 60.0, "bp_pct": 15.0, "peak_occ": 20, "beats": 240000},
            "w": {"util_pct": 35.0, "bp_pct": 7.0, "peak_occ": 6, "beats": 72000},
            "b": {"util_pct": 8.0, "bp_pct": 0.2, "peak_occ": 2, "txns": 18000},
        },
        "throughput": {"read_bps": 1.0e9, "write_bps": 0.45e9},
        "outstanding": {
            "read_peak": 20,
            "read_avg": 10.0,
            "write_peak": 6,
            "write_avg": 2.5,
        },
        "latency_cycles": {
            "ar_to_r_first": {
                "p50": 16,
                "p95": 64,
                "p99": 120,
                "max": 380,
                "hist_log2": [0] * 16,
            },
            "aw_to_b": {
                "p50": 20,
                "p95": 72,
                "p99": 140,
                "max": 460,
                "hist_log2": [0] * 16,
            },
        },
        "errors": {"slverr": 0, "decerr": 0},
    }
    if interface_instance is not None:
        payload["interface_instance"] = interface_instance
    if slave_modport is not None:
        payload["slave_modport"] = slave_modport
    return payload


def _write_axi_perf(path: Path, bundles: list[dict]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "tool": "test",
                "tool_version": "0.1.0",
                "produced_at": "2026-05-21T08:00:00Z",
                "design_top": "tb",
                "duration_cycles": 1000,
                "clock_period_ns": 10.0,
                "bundles": bundles,
                "interconnects": [],
            }
        )
    )
    return path


def _tb_with_interface_dut() -> ModuleTable:
    """``tb`` instantiates an AXI_BUS interface ``u_if`` and a DUT
    ``i_dut`` whose interface port ``slv`` binds ``u_if.Slave`` — the
    same shape as the pulp ``tb_axi_fifo_simple`` demo (master side is
    procedural tb logic, slave side is the DUT instance)."""
    dut = Module(
        name="mem_dut",
        location=_loc("dut.sv"),
        instances=(),
        ports=(
            Port(
                name="slv",
                direction=None,
                type_text="AXI_BUS",
                location=_loc("dut.sv", 2),
                port_kind="interface",
                interface_type="AXI_BUS",
                modport="Slave",
            ),
        ),
        parameters=(),
    )
    tb = Module(
        name="tb",
        location=_loc("tb.sv"),
        instances=(
            Instance(
                name="u_if",
                module_name="AXI_BUS",  # interface → unresolved → blackbox node
                location=_loc("tb.sv", 2),
                param_overrides=(),
                port_connections=(),
            ),
            Instance(
                name="i_dut",
                module_name="mem_dut",
                location=_loc("tb.sv", 3),
                param_overrides=(),
                port_connections=(
                    PortConnection(
                        port_name="slv",
                        net_expr_text="u_if.Slave",
                        location=_loc("tb.sv", 3),
                    ),
                ),
            ),
        ),
        ports=(),
        parameters=(),
    )
    table = ModuleTable()
    table.modules_by_name["tb"] = tb
    table.modules_by_name["mem_dut"] = dut
    return table


def _render(table: ModuleTable, bundles: list[dict]) -> dict:
    root = build_hierarchy(table, "tb")
    with tempfile.TemporaryDirectory() as td:
        axi_perf = load_axi_perf_map(_write_axi_perf(Path(td) / "axi.json", bundles))
    sink = io.StringIO()
    json_render.render(root, sink, axi_perf_map=axi_perf, embed_layout=False)
    return json.loads(sink.getvalue())


def test_interface_bundle_decorates_dut_pin() -> None:
    payload = _render(
        _tb_with_interface_dut(),
        [
            _bundle_payload(
                master="tb",  # procedural master → no instance node
                slave="tb.i_dut",
                interface_instance="tb.u_if",
                slave_modport="Slave",
            )
        ],
    )
    assert "axi-perf" in payload["overlays_present"]
    dut = next(n for n in payload["nodes"] if n["id"] == "tb.i_dut")
    pins = dut["overlays"]["axi-perf"]["bundle_pins"]
    assert len(pins) == 1
    pin = pins[0]
    assert pin["port"] == "slv"
    assert pin["interface_instance"] == "tb.u_if"
    assert pin["modport"] == "Slave"
    assert pin["role"] == "slave"
    # The DUT is the slave; its peer is the (procedural) master endpoint.
    assert pin["peer"] == "tb"
    # Full bundle block rides along so the SPA can paint width/colour/glyph.
    assert pin["bundle"]["name"] == "tb_to_dut"
    assert pin["bundle"]["channels"]["r"]["beats"] == 240000


def test_regex_bundle_without_interface_instance_emits_no_pin() -> None:
    """A regex-detected bundle (no ``interface_instance``) must not
    attach to any interface pin, even when the node has interface ports
    — graceful degradation / no false positives."""
    payload = _render(
        _tb_with_interface_dut(),
        [_bundle_payload(master="tb.u_cpu", slave="tb.u_dram")],
    )
    for node in payload["nodes"]:
        block = node["overlays"].get("axi-perf")
        assert block is None or "bundle_pins" not in block


def _tb_with_flat_dut() -> ModuleTable:
    """``tb`` instantiates ``dut`` whose AXI ports are NOT visible to the
    parser (macro-generated). The manifest describes them instead. Same
    tb_top shape as demo_axi_2x2: dut is the real port owner, the other
    endpoint is the procedural-TB scope (``tb``)."""
    dut = Module(
        name="axi_2x2",
        location=_loc("dut.sv"),
        instances=(),
        ports=(
            Port(
                name="clk",
                direction="input",
                type_text="logic",
                location=_loc("dut.sv", 2),
            ),
            Port(
                name="rst_n",
                direction="input",
                type_text="logic",
                location=_loc("dut.sv", 3),
            ),
        ),
        parameters=(),
    )
    tb = Module(
        name="tb",
        location=_loc("tb.sv"),
        instances=(
            Instance(
                name="dut",
                module_name="axi_2x2",
                location=_loc("tb.sv", 2),
                param_overrides=(),
                port_connections=(
                    PortConnection(
                        port_name="clk", net_expr_text="clk", location=_loc("tb.sv", 2)
                    ),
                ),
            ),
        ),
        ports=(),
        parameters=(),
    )
    table = ModuleTable()
    table.modules_by_name["tb"] = tb
    table.modules_by_name["axi_2x2"] = dut
    return table


def test_manifest_described_bundle_synthesizes_pin_on_dut() -> None:
    """A bundle whose ports the CST can't see attaches to the node named
    by master/slave path, synthesized from the manifest — no interface
    port, no system_view stub."""
    payload = _render(
        _tb_with_flat_dut(),
        [
            # in0: dut is the slave; master is the procedural tb scope.
            _bundle_payload(name="in0", master="tb", slave="tb.dut"),
            # out0: dut is the master; slave is the procedural tb scope.
            _bundle_payload(name="out0", master="tb.dut", slave="tb"),
        ],
    )
    dut = next(n for n in payload["nodes"] if n["id"] == "tb.dut")
    pins = {p["port"]: p for p in dut["overlays"]["axi-perf"]["bundle_pins"]}
    assert set(pins) == {"in0", "out0"}
    assert pins["in0"]["role"] == "slave" and pins["in0"]["peer"] == "tb"
    assert pins["in0"]["synthetic"] is True
    assert pins["out0"]["role"] == "master" and pins["out0"]["peer"] == "tb"
    # The procedural-TB scope (an ancestor of dut) must NOT get a pin.
    tb = next(n for n in payload["nodes"] if n["id"] == "tb")
    block = tb["overlays"].get("axi-perf")
    assert block is None or "bundle_pins" not in block


def test_fully_unmatched_bundle_emits_no_pin() -> None:
    """A bundle that matches neither an interface instance NOR any node's
    master/slave path produces no pin (no spurious attach)."""
    payload = _render(
        _tb_with_interface_dut(),
        [
            _bundle_payload(
                master="tb.ghost_m",
                slave="tb.ghost_s",
                interface_instance="tb.does_not_exist",
                slave_modport="Slave",
            )
        ],
    )
    dut = next(n for n in payload["nodes"] if n["id"] == "tb.i_dut")
    block = dut["overlays"].get("axi-perf")
    assert block is None or "bundle_pins" not in block
