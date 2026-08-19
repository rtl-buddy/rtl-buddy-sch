"""End-to-end coverage for the ``rbsch`` pragma CLI surface (#159).

The unit tests in ``test_hints.py`` build their own ``ModuleTable``,
so they can't prove the one thing most likely to break silently:
that the path string :func:`rtl_buddy_view.hints.scan_pragmas` is
keyed by is byte-identical to the ``SourceLocation.file`` the Verible
frontend records. If those ever diverge, every pragma resolves to
nothing and the only symptom is a diagram that ignores its author.
These tests are therefore Verible-gated and drive the real CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary

FIXTURES = Path(__file__).parent / "fixtures" / "pragma_demo"
FILELIST = FIXTURES / "files.f"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _render(*args: str) -> subprocess.CompletedProcess[str]:
    return _run("--top", "prg_top", "--filelist", str(FILELIST), *args)


def _node_paths(payload: dict) -> list[str]:
    return [n["id"] for n in payload["nodes"]]


# --- association proven end to end ------------------------------------------


def test_pragmas_are_scanned_by_default_in_every_format() -> None:
    """The graph transform is renderer-independent — JSON is the
    machine-readable witness for what tree / dot / mermaid also see."""
    result = _render("--format", "json")
    assert result.returncode == 0, result.stderr
    paths = _node_paths(json.loads(result.stdout))
    assert paths == [
        "prg_top",
        "prg_top.u_csr",
        "prg_top.u_csr.u_sync",
        "prg_top.u_engine",
    ]


def test_no_pragmas_restores_the_design_as_written() -> None:
    result = _render("--format", "json", "--no-pragmas")
    assert result.returncode == 0, result.stderr
    paths = _node_paths(json.loads(result.stdout))
    # hide → u_tieoff back; collapse → u_engine's stages back;
    # module leaf → prg_sync's flop back.
    assert "prg_top.u_tieoff" in paths
    assert "prg_top.u_engine.u_stage0" in paths
    assert "prg_top.u_csr.u_sync.u_ff0" in paths


def test_clean_fixture_produces_no_hint_warnings() -> None:
    result = _render("--format", "json")
    assert "hints:" not in result.stderr


def test_hidden_and_collapsed_nodes_are_gone_from_the_tree_too() -> None:
    result = _render("--format", "tree")
    assert result.returncode == 0, result.stderr
    assert "u_csr" in result.stdout
    assert "u_tieoff" not in result.stdout
    assert "u_stage0" not in result.stdout
    assert "u_ff0" not in result.stdout


def test_labels_render_in_dot_ahead_of_the_instance_name() -> None:
    result = _render("--format", "dot")
    assert result.returncode == 0, result.stderr
    assert r"CSR block\lu_csr\lprg_csr\l" in result.stdout
    assert r"ALU datapath\lu_engine\lprg_engine\l" in result.stdout


def test_labels_render_in_block_mode_html_labels() -> None:
    result = _render("--format", "dot", "--block-diagram")
    assert result.returncode == 0, result.stderr
    assert "<B>CSR block</B>" in result.stdout
    assert "<B>ALU datapath</B>" in result.stdout


def test_json_output_gains_no_new_keys() -> None:
    """``display_label`` is deliberately not in view.json (phase 1)."""
    payload = json.loads(_render("--format", "json").stdout)
    assert all("display_label" not in node for node in payload["nodes"])


# --- sidecar overlay --------------------------------------------------------


def test_sidecar_overrides_the_in_source_pragma() -> None:
    result = _render(
        "--format", "json", "--overlay", f"hints={FIXTURES / 'hints.json'}"
    )
    assert result.returncode == 0, result.stderr
    paths = _node_paths(json.loads(result.stdout))
    # The sidecar revokes u_engine's in-source `collapse`.
    assert "prg_top.u_engine.u_stage0" in paths
    # …while leaving the pragma-only hide + module leaf in force.
    assert "prg_top.u_tieoff" not in paths
    assert "prg_top.u_csr.u_sync.u_ff0" not in paths


def test_sidecar_label_beats_the_in_source_label() -> None:
    result = _render("--format", "dot", "--overlay", f"hints={FIXTURES / 'hints.json'}")
    assert "engine (sidecar)" in result.stdout
    assert "ALU datapath" not in result.stdout


def test_sidecar_applies_with_pragma_scanning_disabled() -> None:
    result = _render(
        "--format",
        "dot",
        "--no-pragmas",
        "--overlay",
        f"hints={FIXTURES / 'hints.json'}",
    )
    assert result.returncode == 0, result.stderr
    assert "engine (sidecar)" in result.stdout
    # `--no-pragmas` is scoped to in-source scanning only.
    assert "CSR block" not in result.stdout


def test_malformed_sidecar_exits_1_with_the_overlay_prefix(tmp_path: Path) -> None:
    bad = tmp_path / "hints.json"
    bad.write_text('{"schema_version": "9.0"}')
    result = _render("--format", "json", "--overlay", f"hints={bad}")
    assert result.returncode == 1
    assert "overlay hints:" in result.stderr


def test_hints_overlay_is_listed() -> None:
    result = _run("--list-overlays")
    assert "hints\t1.1\t(built-in)" in result.stdout


# --- warnings ---------------------------------------------------------------


def test_unknown_pragma_key_warns_on_stderr_and_still_renders(
    tmp_path: Path,
) -> None:
    src = tmp_path / "warn_top.sv"
    src.write_text(
        "module warn_top;\n"
        "  // rbsch: collpase\n"
        "  warn_sub u_sub ();\n"
        "endmodule\n"
        "module warn_sub;\n"
        "endmodule\n"
    )
    filelist = tmp_path / "files.f"
    filelist.write_text("warn_top.sv\n")
    result = _run("--top", "warn_top", "--filelist", str(filelist), "--format", "tree")
    assert result.returncode == 0, result.stderr
    assert "hints: " in result.stderr
    assert "collpase" in result.stderr
    assert "clock, collapse, data, hide, leaf, main, reset, side" in result.stderr
    assert "u_sub" in result.stdout


# --- phase 2: net classification end to end ---------------------------------

NET_FIXTURES = Path(__file__).parent / "fixtures" / "net_hints_demo"
NET_FILELIST = NET_FIXTURES / "files.f"

_MAIN_STYLE = "penwidth=2.2"
_SIDE_STYLE = 'color="#94a3b8", penwidth=0.6'


def _render_net(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "--top",
        "nh_top",
        "--filelist",
        str(NET_FILELIST),
        "--format",
        "dot",
        "--block-diagram",
        *args,
    )


def _edge_line(dot: str, net: str) -> str:
    lines = [ln for ln in dot.splitlines() if f'xlabel="{net}' in ln]
    assert len(lines) == 1, f"expected one {net} edge, got: {lines}"
    return lines[0]


def test_net_pragmas_reshape_the_block_diagram() -> None:
    result = _render_net()
    assert result.returncode == 0, result.stderr
    dot = result.stdout
    # `tick` (rbsch: clock) no longer leaks into the dataflow.
    assert '"_in_tick" ->' not in dot
    # `clk_result` (rbsch: data) is rescued from the clock filter.
    assert '-> "_out_clk_result"' in dot
    # `stage_q` (rbsch: main) is emboldened; `done_status`
    # (rbsch: side) is thinned and grayed.
    assert _MAIN_STYLE in _edge_line(dot, "busy_status")  # bundled with stage_q
    assert _SIDE_STYLE in _edge_line(dot, "done_status")
    assert "hints:" not in result.stderr


def test_no_pragmas_restores_the_unclassified_dataflow() -> None:
    result = _render_net("--no-pragmas")
    assert result.returncode == 0, result.stderr
    dot = result.stdout
    assert '"_in_tick" ->' in dot
    assert '-> "_out_clk_result"' not in dot
    assert _MAIN_STYLE not in dot
    assert _SIDE_STYLE not in dot


def test_net_sidecar_overrides_the_in_source_pragmas() -> None:
    result = _render_net("--overlay", f"hints={NET_FIXTURES / 'net_hints.json'}")
    assert result.returncode == 0, result.stderr
    dot = result.stdout
    # Sidecar revokes `tick`'s clock classification (data) — the
    # edges return — and demotes `stage_q` from main to side.
    assert '"_in_tick" ->' in dot
    assert _SIDE_STYLE in _edge_line(dot, "busy_status")
    # Untouched in-source hints keep working through the merge.
    assert '-> "_out_clk_result"' in dot


def test_net_hints_reach_the_elk_export_structurally() -> None:
    """The SPA canvas and the dot figure must agree on what exists."""
    result = _run(
        "--top",
        "nh_top",
        "--filelist",
        str(NET_FILELIST),
        "--format",
        "elk",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    def edge_nets(node: dict) -> list[str]:
        nets = [n for e in node["edges"] for n in e["rb"]["nets"]]
        for child in node["children"]:
            nets.extend(edge_nets(child))
        return nets

    nets = edge_nets(payload)
    assert "tick" not in nets
    assert "clk_result" in nets


def test_net_hints_leave_the_hierarchy_formats_untouched() -> None:
    """Classification and emphasis are dataflow-only: tree output is
    byte-identical with and without the net pragmas."""
    with_hints = _run(
        "--top", "nh_top", "--filelist", str(NET_FILELIST), "--format", "tree"
    )
    without = _run(
        "--top",
        "nh_top",
        "--filelist",
        str(NET_FILELIST),
        "--format",
        "tree",
        "--no-pragmas",
    )
    assert with_hints.returncode == 0 and without.returncode == 0
    assert with_hints.stdout == without.stdout


# --- phase 3: bundles end to end --------------------------------------------

BUNDLE_FIXTURES = Path(__file__).parent / "fixtures" / "bundle_demo"
BUNDLE_FILELIST = BUNDLE_FIXTURES / "files.f"


def _render_bundle(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "--top",
        "bd_top",
        "--filelist",
        str(BUNDLE_FILELIST),
        "--format",
        "dot",
        "--block-diagram",
        *args,
    )


def test_bundle_renders_one_named_thick_edge_in_the_data_direction() -> None:
    result = _render_bundle()
    assert result.returncode == 0, result.stderr
    dot = result.stdout
    line = _edge_line(dot, "cmd_bus")
    assert '"bd_top.u_prod" -> "bd_top.u_cons"' in line
    assert "penwidth=1.6" in line
    # The member nets vanish from the label, and the folded return
    # path leaves no reverse edge behind.
    assert 'xlabel="cmd_valid' not in dot
    assert 'xlabel="cmd_ready' not in dot
    assert '"bd_top.u_cons" -> "bd_top.u_prod"' not in dot
    assert "hints:" not in result.stderr


def test_no_pragmas_restores_the_unbundled_handshake() -> None:
    result = _render_bundle("--no-pragmas")
    assert result.returncode == 0, result.stderr
    dot = result.stdout
    assert "cmd_bus" not in dot
    assert '"bd_top.u_cons" -> "bd_top.u_prod"' in dot


# --- phase 4: layout hints end to end ---------------------------------------

LAYOUT_FIXTURES = Path(__file__).parent / "fixtures" / "layout_demo"
LAYOUT_FILELIST = LAYOUT_FIXTURES / "files.f"


def _render_layout(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "--top",
        "ld_top",
        "--filelist",
        str(LAYOUT_FILELIST),
        "--format",
        "dot",
        "--block-diagram",
        *args,
    )


def test_group_hint_draws_a_dashed_virtual_container() -> None:
    result = _render_layout()
    assert result.returncode == 0, result.stderr
    dot = result.stdout
    start = dot.index("subgraph cluster_grp_ld_top__frontend {")
    body = dot[start : dot.index("\n  }", start)]
    assert 'label="frontend"' in body
    assert 'style="rounded,dashed"' in body
    # Both members emit inside the container; the ungrouped stage
    # stays outside it.
    assert '"ld_top.u_fetch"' in body
    assert '"ld_top.u_decode"' in body
    assert '"ld_top.u_execute"' not in body
    assert "hints:" not in result.stderr


def test_rank_hints_chain_invisible_ordering_edges() -> None:
    dot = _render_layout().stdout
    assert '"ld_top.u_fetch" -> "ld_top.u_decode" [style=invis, weight=8];' in dot
    assert '"ld_top.u_decode" -> "ld_top.u_execute" [style=invis, weight=8];' in dot


def test_no_pragmas_drops_the_layout_scaffolding() -> None:
    dot = _render_layout("--no-pragmas").stdout
    assert "cluster_grp_" not in dot
    assert "[style=invis, weight=8]" not in dot


# --- phase 5: blackbox pin directions end to end ----------------------------

BB_FIXTURES = Path(__file__).parent / "fixtures" / "blackbox_demo"
BB_FILELIST = BB_FIXTURES / "files.f"


def _render_bb(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(
        "--top",
        "vb_top",
        "--filelist",
        str(BB_FILELIST),
        "--format",
        "dot",
        "--block-diagram",
        *args,
    )


def test_pin_hint_recovers_the_blackbox_output_edge() -> None:
    result = _render_bb()
    assert result.returncode == 0, result.stderr
    assert '"vb_top.u_rom" -> "vb_top.u_reg"' in result.stdout
    assert "hints:" not in result.stderr


def test_without_the_hint_the_unknowable_edge_stays_undrawn() -> None:
    """Better an under-drawn edge than a fabricated direction — the
    pre-hint behavior is the safe baseline the pragma opts out of."""
    dot = _render_bb("--no-pragmas").stdout
    assert '"vb_top.u_rom" -> "vb_top.u_reg"' not in dot


def test_module_level_sidecar_pins_vendor_ip_without_source_edits() -> None:
    """The sidecar's natural phase-5 spelling: key the blackbox by
    module name — its source can't carry a comment."""
    dot = _render_bb(
        "--no-pragmas",
        "--overlay",
        f"hints={BB_FIXTURES / 'vendor.hints.json'}",
    ).stdout
    assert '"vb_top.u_rom" -> "vb_top.u_reg"' in dot


# --- #180: presentation reaches the ELK payload and view.json ---------------


def _elk_edges(node: dict) -> list[dict]:
    out = list(node["edges"])
    for child in node["children"]:
        out.extend(_elk_edges(child))
    return out


def test_elk_edges_carry_emphasis_and_bundle() -> None:
    result = _run(
        "--top", "bd_top", "--filelist", str(BUNDLE_FILELIST), "--format", "elk"
    )
    assert result.returncode == 0, result.stderr
    edges = _elk_edges(json.loads(result.stdout))
    bundled = [e for e in edges if e["rb"]["bundle"] == "cmd_bus"]
    assert len(bundled) == 1
    # The folded return path arrives folded: all three nets, one edge.
    assert bundled[0]["rb"]["nets"] == ["cmd_data", "cmd_ready", "cmd_valid"]


def test_elk_nodes_carry_display_label() -> None:
    result = _render("--format", "elk")
    assert result.returncode == 0, result.stderr

    def labels(node: dict) -> dict[str, object]:
        out = {node["id"]: node["rb"]["display_label"]}
        for child in node["children"]:
            out.update(labels(child))
        return out

    by_id = labels(json.loads(result.stdout))
    assert by_id["prg_top.u_csr"] == "CSR block"
    assert by_id["prg_top"] is None


def test_view_json_embedded_elk_gets_the_hints_too() -> None:
    """The SPA loads view.json's layout.elk, not --format elk; the two
    must not diverge."""
    result = _run(
        "--top",
        "nh_top",
        "--filelist",
        str(NET_FILELIST),
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    edges = _elk_edges(payload["layout"]["elk"])
    nets = [n for e in edges for n in e["rb"]["nets"]]
    assert "tick" not in nets  # rbsch: clock — suppressed
    assert "clk_result" in nets  # rbsch: data — rescued
    emphases = {e["rb"]["emphasis"] for e in edges}
    assert {"main", "side"} <= emphases
