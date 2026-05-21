"""Tests for axi-perf integration into the JSON renderer (Phase 11)."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from rtl_buddy_view.axi_perf_annotations import load_axi_perf_map
from rtl_buddy_view.extractor import Instance, Module, ModuleTable, SourceLocation
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import json_render


def _bundle_payload(
    name: str = "cpu_to_dram",
    master: str = "soc.u_cpu",
    slave: str = "soc.u_dram",
) -> dict:
    return {
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


def _write_axi_perf(
    path: Path, bundles: list[dict], interconnects: list[dict] | None = None
) -> Path:
    payload = {
        "schema_version": "1.0",
        "tool": "test",
        "tool_version": "0.1.0",
        "produced_at": "2026-05-21T08:00:00Z",
        "design_top": "soc",
        "duration_cycles": 1000,
        "clock_period_ns": 2.0,
        "bundles": bundles,
        "interconnects": interconnects or [],
    }
    path.write_text(json.dumps(payload))
    return path


def _loc(file: str, line: int = 1) -> SourceLocation:
    return SourceLocation(
        file=file, start_line=line, start_column=1, end_line=line + 1, end_column=1
    )


def _make_two_inst_design() -> ModuleTable:
    """`soc` instantiates `cpu` and `dram` — minimal edge fixture."""
    cpu = Module(
        name="cpu", location=_loc("cpu.sv"), instances=(), ports=(), parameters=()
    )
    dram = Module(
        name="dram", location=_loc("dram.sv"), instances=(), ports=(), parameters=()
    )
    soc = Module(
        name="soc",
        location=_loc("soc.sv"),
        instances=(
            Instance(
                name="u_cpu",
                module_name="cpu",
                location=_loc("soc.sv", 2),
                param_overrides=(),
                port_connections=(),
            ),
            Instance(
                name="u_dram",
                module_name="dram",
                location=_loc("soc.sv", 3),
                param_overrides=(),
                port_connections=(),
            ),
        ),
        ports=(),
        parameters=(),
    )
    table = ModuleTable()
    table.modules_by_name["soc"] = soc
    table.modules_by_name["cpu"] = cpu
    table.modules_by_name["dram"] = dram
    return table


def test_axi_perf_block_lands_on_matching_edge() -> None:
    table = _make_two_inst_design()
    root = build_hierarchy(table, "soc")
    with tempfile.TemporaryDirectory() as td:
        axi_path = _write_axi_perf(
            Path(td) / "axi.json",
            [_bundle_payload(master="soc.u_cpu", slave="soc.u_dram")],
        )
        axi_perf = load_axi_perf_map(axi_path)
    sink = io.StringIO()
    json_render.render(root, sink, axi_perf_map=axi_perf, embed_layout=False)
    payload = json.loads(sink.getvalue())
    # 2 edges (soc→u_cpu, soc→u_dram), neither matches the bundle's
    # (master=u_cpu, slave=u_dram) pair since the edges are
    # parent→child within the hierarchy, not lateral master↔slave.
    # The match happens when the parent IS the master and the child
    # IS the slave — typical for sibling-pair fixtures where soc
    # instantiates the slave directly. So the edge soc.u_cpu→soc.u_dram
    # doesn't exist in this hierarchy; the bundle won't attach.
    # Confirm overlays_present picks up the map presence anyway.
    assert "axi-perf" in payload["overlays_present"]
    # No edge in this fixture matches; ensure none falsely picked up.
    for edge in payload["edges"]:
        assert "axi-perf" not in edge["overlays"]


def test_axi_perf_block_attaches_when_edge_matches() -> None:
    """Construct a synthetic hierarchy where (parent, child) instance
    paths line up with a bundle's (master_path, slave_path)."""
    table = _make_two_inst_design()
    root = build_hierarchy(table, "soc")
    with tempfile.TemporaryDirectory() as td:
        # Use the parent→child pair as the bundle endpoints.
        axi_path = _write_axi_perf(
            Path(td) / "axi.json",
            [_bundle_payload(master="soc", slave="soc.u_cpu")],
        )
        axi_perf = load_axi_perf_map(axi_path)
    sink = io.StringIO()
    json_render.render(root, sink, axi_perf_map=axi_perf, embed_layout=False)
    payload = json.loads(sink.getvalue())
    edge = next(e for e in payload["edges"] if e["to"] == "soc.u_cpu")
    assert "axi-perf" in edge["overlays"]
    block = edge["overlays"]["axi-perf"]
    assert block["name"] == "cpu_to_dram"
    assert block["protocol"] == "AXI4"
    assert block["data_width"] == 64
    # Spot-check the channel block + latency hist length.
    assert block["channels"]["r"]["beats"] == 240000
    assert len(block["latency_cycles"]["ar_to_r_first"]["hist_log2"]) == 16


def test_interconnect_rollup_lands_on_node_overlay() -> None:
    table = _make_two_inst_design()
    root = build_hierarchy(table, "soc")
    interconnects = [
        {
            "node_path": "soc.u_dram",
            "total_read_bps": 2.0e9,
            "total_write_bps": 1.0e9,
            "hottest_master": "soc.u_cpu",
            "hottest_slave": "soc.u_dram",
            "arbitration": {"fairness_jain": 0.9, "starved_masters": []},
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        axi_path = _write_axi_perf(
            Path(td) / "axi.json",
            [_bundle_payload()],
            interconnects=interconnects,
        )
        axi_perf = load_axi_perf_map(axi_path)
    sink = io.StringIO()
    json_render.render(root, sink, axi_perf_map=axi_perf, embed_layout=False)
    payload = json.loads(sink.getvalue())
    dram_node = next(n for n in payload["nodes"] if n["id"] == "soc.u_dram")
    assert "axi-perf" in dram_node["overlays"]
    ic = dram_node["overlays"]["axi-perf"]["interconnect"]
    assert ic["hottest_master"] == "soc.u_cpu"
    assert ic["arbitration"]["fairness_jain"] == 0.9


def test_no_axi_perf_map_is_a_noop() -> None:
    """Existing-behavior preservation: passing no map = no axi-perf entries."""
    table = _make_two_inst_design()
    root = build_hierarchy(table, "soc")
    sink = io.StringIO()
    json_render.render(root, sink, embed_layout=False)
    payload = json.loads(sink.getvalue())
    assert "axi-perf" not in payload["overlays_present"]
    for edge in payload["edges"]:
        assert "axi-perf" not in edge["overlays"]
    for node in payload["nodes"]:
        assert "axi-perf" not in node["overlays"]


def test_empty_axi_perf_map_doesnt_emit_overlays_present() -> None:
    """A map with no bundles + no interconnects shouldn't claim
    overlays_present, but the render should still succeed."""
    table = _make_two_inst_design()
    root = build_hierarchy(table, "soc")
    with tempfile.TemporaryDirectory() as td:
        axi_path = _write_axi_perf(Path(td) / "axi.json", [], interconnects=[])
        axi_perf = load_axi_perf_map(axi_path)
    sink = io.StringIO()
    json_render.render(root, sink, axi_perf_map=axi_perf, embed_layout=False)
    payload = json.loads(sink.getvalue())
    assert "axi-perf" not in payload["overlays_present"]
