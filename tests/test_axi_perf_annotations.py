"""Tests for the axi-perf.json loader (Phase 11 first slice, #60)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtl_buddy_view.axi_perf_annotations import (
    AxiPerfAnnotationsError,
    AxiPerfMap,
    load_axi_perf_map,
)
from rtl_buddy_view.overlays import default_registry
from rtl_buddy_view.overlays.axi_perf import AxiPerfOverlay


def _minimal_bundle(
    name: str = "cpu_to_dram", master: str = "soc.u_cpu", slave: str = "soc.u_dram"
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
            "ar": {"util_pct": 32.1, "bp_pct": 4.2, "peak_occ": 12, "txns": 41023},
            "aw": {"util_pct": 18.7, "bp_pct": 1.1, "peak_occ": 6, "txns": 22987},
            "r": {"util_pct": 71.5, "bp_pct": 22.4, "peak_occ": 28, "beats": 328184},
            "w": {"util_pct": 41.3, "bp_pct": 8.9, "peak_occ": 9, "beats": 91948},
            "b": {"util_pct": 9.8, "bp_pct": 0.3, "peak_occ": 3, "txns": 22987},
        },
        "throughput": {"read_bps": 1.31e9, "write_bps": 0.59e9},
        "outstanding": {
            "read_peak": 28,
            "read_avg": 12.4,
            "write_peak": 9,
            "write_avg": 3.7,
        },
        "latency_cycles": {
            "ar_to_r_first": {
                "p50": 18,
                "p95": 76,
                "p99": 142,
                "max": 410,
                "hist_log2": [0] * 16,
            },
            "aw_to_b": {
                "p50": 22,
                "p95": 80,
                "p99": 160,
                "max": 512,
                "hist_log2": [0] * 16,
            },
        },
        "errors": {"slverr": 0, "decerr": 2},
    }


def _minimal_payload(*, bundles: list[dict] | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "tool": "rtl-buddy-axi-profiler",
        "tool_version": "0.1.0",
        "produced_at": "2026-05-21T08:00:00Z",
        "design_top": "soc",
        "duration_cycles": 1000,
        "clock_period_ns": 2.0,
        "bundles": bundles if bundles is not None else [_minimal_bundle()],
        "interconnects": [],
    }


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "axi-perf.json"
    p.write_text(json.dumps(payload))
    return p


def test_load_minimal_payload(tmp_path: Path) -> None:
    path = _write(tmp_path, _minimal_payload())
    m = load_axi_perf_map(path)
    assert isinstance(m, AxiPerfMap)
    assert m.schema_version == "1.0"
    assert m.design_top == "soc"
    assert len(m.bundles) == 1
    assert m.bundles[0].master_path == "soc.u_cpu"
    assert m.bundles[0].channels["r"].beats == 328184
    assert m.bundles[0].channels["ar"].txns == 41023


def test_bundle_at_edge_lookup(tmp_path: Path) -> None:
    path = _write(tmp_path, _minimal_payload())
    m = load_axi_perf_map(path)
    b = m.bundle_at_edge("soc.u_cpu", "soc.u_dram")
    assert b is not None
    assert b.name == "cpu_to_dram"
    # Missing pair returns None, not an error.
    assert m.bundle_at_edge("nope", "nope") is None


def test_hierarchical_children_indexed(tmp_path: Path) -> None:
    parent = _minimal_bundle(name="cpu_to_xbar", master="soc.u_cpu", slave="soc.u_xbar")
    child = _minimal_bundle(
        name="xbar_to_dram", master="soc.u_xbar", slave="soc.u_dram"
    )
    parent["children"] = [child]
    path = _write(tmp_path, _minimal_payload(bundles=[parent]))
    m = load_axi_perf_map(path)
    assert m.bundle_at_edge("soc.u_cpu", "soc.u_xbar") is not None
    assert m.bundle_at_edge("soc.u_xbar", "soc.u_dram") is not None


def test_interconnect_lookup(tmp_path: Path) -> None:
    payload = _minimal_payload()
    payload["interconnects"] = [
        {
            "node_path": "soc.u_xbar",
            "total_read_bps": 4.2e9,
            "total_write_bps": 2.8e9,
            "hottest_master": "soc.u_cpu",
            "hottest_slave": "soc.u_dram",
            "arbitration": {"fairness_jain": 0.78, "starved_masters": []},
        }
    ]
    path = _write(tmp_path, payload)
    m = load_axi_perf_map(path)
    ic = m.interconnect_at("soc.u_xbar")
    assert ic is not None
    assert ic.hottest_master == "soc.u_cpu"
    assert m.interconnect_at("missing") is None


def test_rejects_wrong_schema_major(tmp_path: Path) -> None:
    payload = _minimal_payload()
    payload["schema_version"] = "2.0"
    path = _write(tmp_path, payload)
    with pytest.raises(AxiPerfAnnotationsError) as info:
        load_axi_perf_map(path)
    assert "schema_version" in str(info.value)


def test_rejects_missing_required_field(tmp_path: Path) -> None:
    payload = _minimal_payload()
    del payload["design_top"]
    path = _write(tmp_path, payload)
    with pytest.raises(AxiPerfAnnotationsError):
        load_axi_perf_map(path)


def test_rejects_short_hist_log2(tmp_path: Path) -> None:
    payload = _minimal_payload()
    payload["bundles"][0]["latency_cycles"]["ar_to_r_first"]["hist_log2"] = [0] * 15
    path = _write(tmp_path, payload)
    with pytest.raises(AxiPerfAnnotationsError):
        load_axi_perf_map(path)


def test_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "axi-perf.json"
    path.write_text("{this is not json}")
    with pytest.raises(AxiPerfAnnotationsError):
        load_axi_perf_map(path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AxiPerfAnnotationsError):
        load_axi_perf_map(tmp_path / "does-not-exist.json")


def test_is_empty_when_no_bundles(tmp_path: Path) -> None:
    payload = _minimal_payload(bundles=[])
    path = _write(tmp_path, payload)
    m = load_axi_perf_map(path)
    assert m.is_empty is True


def test_overlay_registered_in_default_registry() -> None:
    registry = default_registry()
    assert "axi-perf" in registry.names()
    overlay = registry.get("axi-perf")
    assert overlay.name == "axi-perf"
    assert overlay.schema_version == "1.0"


def test_overlay_load_returns_axi_perf_map(tmp_path: Path) -> None:
    """The Overlay protocol's load() must round-trip to AxiPerfMap."""
    path = _write(tmp_path, _minimal_payload())
    overlay = AxiPerfOverlay()
    annotation = overlay.load(path)
    assert isinstance(annotation, AxiPerfMap)
    assert annotation.bundle_at_edge("soc.u_cpu", "soc.u_dram") is not None
