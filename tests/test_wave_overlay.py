"""Phase 8 wave overlay tests (rtl-buddy-view#21).

Three layers covered:

* :mod:`rtl_buddy_view._vcd_reader` — pure-Python VCD parser. Unit
  tests against the in-tree ``counter.vcd`` fixture; happy path,
  pre-zero sampling, end-sentinel, and a couple of malformed inputs.
* :mod:`rtl_buddy_view.wave_annotations` — CLI-spec parser +
  hierarchy-suffix join. Tests on the public surface only; the VCD
  reader is exercised transitively.
* :mod:`rtl_buddy_view.overlays.wave` — registry registration +
  the JSON renderer's per-node ``overlays.wave.ports[]`` emission.
  End-to-end smoke via the CLI entry point.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rtl_buddy_view._vcd_reader import (
    VcdParseError,
    parse_time_spec,
    sample_vcd,
)
from rtl_buddy_view.wave_annotations import (
    WaveAnnotationsError,
    WaveMap,
    load_wave_map,
    parse_wave_overlay_spec,
)
from rtl_buddy_view.overlays import default_registry
from rtl_buddy_view.overlays.wave import WaveOverlay


FIXTURE = Path(__file__).parent / "fixtures" / "wave_counter"


# ---------------------------------------------------------------------------
# VCD reader
# ---------------------------------------------------------------------------


def test_parse_time_spec_units():
    assert parse_time_spec("100ns") == 100_000_000
    assert parse_time_spec("12500fs") == 12_500
    assert parse_time_spec("1.5us") == 1_500_000_000
    assert parse_time_spec("END") is None
    assert parse_time_spec("end") is None


def test_parse_time_spec_rejects_bad_input():
    with pytest.raises(VcdParseError):
        parse_time_spec("100")
    with pytest.raises(VcdParseError):
        parse_time_spec("xyz")
    with pytest.raises(VcdParseError):
        parse_time_spec("100mins")


def test_sample_vcd_at_t10_returns_first_transition():
    snap = sample_vcd(FIXTURE / "counter.vcd", target_fs=10_000_000)  # 10 ns
    # Timescale is 1 ns → 1_000_000 fs / tick.
    assert snap.timescale_fs == 1_000_000
    assert snap.t_fs == 10_000_000
    # At t=10ns: clk transitioned 0→1, rst_n is 1, q is 0x01.
    assert snap.signals["tb.dut.clk"] == "1"
    assert snap.signals["tb.dut.rst_n"] == "1"
    assert snap.signals["tb.dut.q"] == "00000001"
    # The nested u_ff scope mirrors the same ids — bit-identical
    # values, distinct hierarchy paths.
    assert snap.signals["tb.dut.u_ff.clk"] == "1"
    assert snap.signals["tb.dut.u_ff.q"] == "00000001"


def test_sample_vcd_with_end_sentinel():
    snap = sample_vcd(FIXTURE / "counter.vcd", target_fs=None)
    # Last record in the file is at #50 → 50 ns = 50_000_000 fs.
    assert snap.t_fs == 50_000_000
    assert snap.signals["tb.dut.q"] == "00000011"


def test_sample_vcd_before_any_transition():
    # ``counter.vcd`` first records values at #0 via $dumpvars. A
    # target of t<0 (impossible in normal flow but defensive) still
    # finds no transitions; ``current`` ends up empty for unsampled
    # signals.
    snap = sample_vcd(FIXTURE / "counter.vcd", target_fs=0)
    # #0 sets dumpvars values → those land.
    assert snap.t_fs == 0
    assert snap.signals["tb.dut.clk"] == "0"
    assert snap.signals["tb.dut.q"] == "00000000"


def test_sample_vcd_missing_enddefinitions():
    with pytest.raises(VcdParseError, match="no \\$enddefinitions"):
        from rtl_buddy_view._vcd_reader import _parse

        _parse("garbage", target_fs=None)


def test_sample_vcd_no_var_declarations():
    text = "$timescale 1 ns $end\n$enddefinitions $end\n#0\n"
    from rtl_buddy_view._vcd_reader import _parse

    with pytest.raises(VcdParseError, match="no \\$var declarations"):
        _parse(text, target_fs=None)


# ---------------------------------------------------------------------------
# wave_annotations
# ---------------------------------------------------------------------------


def test_parse_wave_overlay_spec_with_time():
    path, time_spec = parse_wave_overlay_spec(str(FIXTURE / "counter.vcd:10ns"))
    assert path == FIXTURE / "counter.vcd"
    assert time_spec == "10ns"


def test_parse_wave_overlay_spec_with_end_sentinel():
    path, time_spec = parse_wave_overlay_spec(str(FIXTURE / "counter.vcd:end"))
    assert path == FIXTURE / "counter.vcd"
    assert time_spec == "end"


def test_parse_wave_overlay_spec_path_only_defaults_to_end():
    path, time_spec = parse_wave_overlay_spec(str(FIXTURE / "counter.vcd"))
    assert path == FIXTURE / "counter.vcd"
    assert time_spec == "end"


def test_parse_wave_overlay_spec_keeps_path_when_trailing_isnt_time():
    # A filename like ``odd:name.vcd`` should not have its colon
    # interpreted as a time separator — the trailing ``name.vcd``
    # doesn't parse as a time spec, so the whole string is the path.
    path, time_spec = parse_wave_overlay_spec("/tmp/odd:name.vcd")
    assert path == Path("/tmp/odd:name.vcd")
    assert time_spec == "end"


def test_load_wave_map_happy_path():
    wm = load_wave_map(f"{FIXTURE / 'counter.vcd'}:10ns")
    assert isinstance(wm, WaveMap)
    assert wm.t_fs == 10_000_000
    # Suffix-match find.
    assert wm.find_for_port("counter.u_ff", "q") == "00000001"
    assert wm.find_for_port("counter.u_ff", "clk") == "1"
    # A missing port returns None — gracefully.
    assert wm.find_for_port("counter.u_ff", "nonexistent") is None


def test_load_wave_map_missing_file_errors():
    with pytest.raises(WaveAnnotationsError, match="not found"):
        load_wave_map("/nowhere/at/all.vcd:10ns")


def test_load_wave_map_bad_time_spec_errors():
    with pytest.raises(WaveAnnotationsError):
        load_wave_map(f"{FIXTURE / 'counter.vcd'}:bogus")


def test_wave_map_find_for_port_walks_prefixes_to_match_testbench_wrapping():
    """The view's node.id is design-relative (``counter.u_ff``); the
    VCD's hier wraps the design under a testbench
    (``tb.dut.u_ff.q``). The lookup must walk node-path prefixes
    until something matches, so the natural designer flow works
    without an explicit ``tb_prefix`` annotation."""
    wm = load_wave_map(f"{FIXTURE / 'counter.vcd'}:10ns")
    # node counter (design top): bare port "q" matches the shortest
    # available path → tb.dut.q (preferred over tb.dut.u_ff.q).
    assert wm.find_for_port("counter", "q") == "00000001"
    # nested instance: drop the "counter" segment → ``u_ff.q``
    # matches ``tb.dut.u_ff.q``.
    assert wm.find_for_port("counter.u_ff", "q") == "00000001"
    assert wm.find_for_port("counter.u_ff", "clk") == "1"
    # A port that doesn't exist in the VCD at all → None.
    assert wm.find_for_port("counter", "ghost") is None


# ---------------------------------------------------------------------------
# overlay registry + end-to-end CLI
# ---------------------------------------------------------------------------


def test_wave_overlay_registered_by_default():
    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)
    overlay = registry.get("wave")
    assert isinstance(overlay, WaveOverlay)
    assert overlay.name == "wave"
    assert overlay.schema_version == "1.0"
    assert warn.getvalue() == ""


@pytest.mark.skipif(
    not (Path(__file__).parent / "fixtures").exists(),
    reason="missing fixtures dir",
)
def test_cli_renders_wave_overlay_into_view_json(tmp_path):
    """End-to-end: ``rtl-buddy-view --overlay wave=...`` populates
    ``node.overlays.wave.ports[]`` in the rendered view.json.

    Requires the Verible binary; the test is skipped when it's not
    on PATH (mirrors the smoke-test pattern).
    """
    pytest.importorskip("typer")
    # Verible is needed for parse_to_modules → skip when absent so
    # the suite stays useful on machines without it. This mirrors
    # tests/test_smoke.py's discovery.
    try:
        from rtl_buddy_view._verible_install import find_binary
    except ImportError:
        pytest.skip("verible not available")
    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible binary not on PATH / vendor/")

    from rtl_buddy_view.cli import app

    runner = CliRunner()
    out_path = tmp_path / "view.json"
    result = runner.invoke(
        app,
        [
            "--top",
            "counter",
            "--filelist",
            str(FIXTURE / "files.f"),
            "--overlay",
            f"wave={FIXTURE / 'counter.vcd'}:10ns",
            "--format",
            "json",
            "--output",
            str(out_path),
        ],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(out_path.read_text())
    assert "wave" in payload["overlays_present"]
    assert payload["overlay_meta"]["wave"]["t_fs"] == "10000000"

    # The counter node should carry the three port values from t=10ns.
    nodes_by_id = {n["id"]: n for n in payload["nodes"]}
    counter = nodes_by_id["counter"]
    wave_block = counter["overlays"]["wave"]
    assert wave_block["t_fs"] == "10000000"
    rows = {r["name"]: r["value"] for r in wave_block["ports"]}
    # 1-bit signals render as ``1'b<v>``; the 8-bit q renders as
    # ``8'b<bits>``.
    assert rows["clk"] == "1'b1"
    assert rows["rst_n"] == "1'b1"
    assert rows["q"] == "8'b00000001"

    # The nested u_ff node should pick up the same values from the
    # ``tb.dut.u_ff`` scope via the suffix-match join.
    u_ff = nodes_by_id["counter.u_ff"]
    rows_uff = {r["name"]: r["value"] for r in u_ff["overlays"]["wave"]["ports"]}
    assert rows_uff["q"] == "8'b00000001"
