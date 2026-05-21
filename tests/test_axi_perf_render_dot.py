"""Tests for axi-perf-map passthrough in the DOT renderer.

The DOT topology is parent → child by hierarchy. AXI bundles connect
siblings (e.g. CPU and DRAM at the same hierarchy level) that DOT
doesn't currently emit as explicit edges. Wiring per-edge bundle
styling into DOT requires synthesizing sibling edges — tracked as a
follow-up to #60.

For now we just confirm:
- The kwarg flows through `render(axi_perf_map=...)` without crash.
- The DOT output is identical with or without a map (no surprise
  style changes).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from rtl_buddy_view.axi_perf_annotations import load_axi_perf_map
from rtl_buddy_view.extractor import Instance, Module, ModuleTable, SourceLocation
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot as dot_render


def _loc(file: str, line: int = 1) -> SourceLocation:
    return SourceLocation(
        file=file, start_line=line, start_column=1, end_line=line + 1, end_column=1
    )


def _make_two_inst_design() -> ModuleTable:
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


def _bundle_payload() -> dict:
    return {
        "name": "cpu_to_dram",
        "master_path": "soc.u_cpu",
        "slave_path": "soc.u_dram",
        "protocol": "AXI4",
        "data_width": 64,
        "id_width": 4,
        "default_view": "parent",
        "channels": {
            "ar": {"util_pct": 30.0, "bp_pct": 2.0, "peak_occ": 8, "txns": 1000},
            "aw": {"util_pct": 15.0, "bp_pct": 1.0, "peak_occ": 4, "txns": 500},
            "r": {"util_pct": 60.0, "bp_pct": 5.0, "peak_occ": 12, "beats": 8000},
            "w": {"util_pct": 35.0, "bp_pct": 3.0, "peak_occ": 6, "beats": 2000},
            "b": {"util_pct": 5.0, "bp_pct": 0.0, "peak_occ": 2, "txns": 500},
        },
        "throughput": {"read_bps": 1.0e9, "write_bps": 0.5e9},
        "outstanding": {
            "read_peak": 12,
            "read_avg": 6.0,
            "write_peak": 4,
            "write_avg": 2.0,
        },
        "latency_cycles": {
            "ar_to_r_first": {
                "p50": 10,
                "p95": 50,
                "p99": 100,
                "max": 200,
                "hist_log2": [0] * 16,
            },
            "aw_to_b": {
                "p50": 20,
                "p95": 60,
                "p99": 120,
                "max": 240,
                "hist_log2": [0] * 16,
            },
        },
        "errors": {"slverr": 0, "decerr": 0},
    }


def _write_axi_perf(tmp: Path) -> Path:
    payload = {
        "schema_version": "1.0",
        "tool": "test",
        "tool_version": "0.1.0",
        "produced_at": "2026-05-21T08:00:00Z",
        "design_top": "soc",
        "duration_cycles": 1000,
        "clock_period_ns": 2.0,
        "bundles": [_bundle_payload()],
        "interconnects": [],
    }
    path = tmp / "axi.json"
    path.write_text(json.dumps(payload))
    return path


def test_dot_accepts_axi_perf_map_kwarg(tmp_path: Path) -> None:
    """Passing axi_perf_map must not crash; output is unchanged for now."""
    table = _make_two_inst_design()
    root = build_hierarchy(table, "soc")
    axi = load_axi_perf_map(_write_axi_perf(tmp_path))

    sink_no_map = io.StringIO()
    dot_render.render(root, sink_no_map)
    sink_with_map = io.StringIO()
    dot_render.render(root, sink_with_map, axi_perf_map=axi)

    # Until DOT sibling-edge synthesis lands (follow-up to #60), the
    # axi-perf map is accepted but doesn't change DOT output.
    assert sink_no_map.getvalue() == sink_with_map.getvalue()
