"""Tests for the Phase 2 clock-domain overlay (renderer side).

The aggregator gets unit-test coverage with a synthetic ``DomainMap``;
the tree and dot renderers get both unit tests (synthetic HierNodes)
and an end-to-end run against the ``two_clock_design`` fixture so the
full pipeline — Verible → graph → annotated render — is exercised.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.annotations import (
    Clock,
    Crossing,
    DomainMap,
    FlopDomain,
    load_domain_map,
)
from rtl_buddy_view.extractor import Instance
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import HierNode, build_hierarchy
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import tree as tree_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "two_clock_design"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


# --- unit: predominant_clock aggregator -------------------------------------


def _flop(path: str, clock: str | None) -> FlopDomain:
    return FlopDomain(instance_path=path, clock=clock, location=None)


def _populated_map(flops: list[FlopDomain]) -> DomainMap:
    return DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        flop_domains=tuple(flops),
    )


def test_predominant_clock_single_clock_wins() -> None:
    m = _populated_map(
        [
            _flop("top.u_a.f1", "clk_a"),
            _flop("top.u_a.f2", "clk_a"),
            _flop("top.u_a.f3", "clk_b"),
        ]
    )
    assert m.predominant_clock("top.u_a") == "clk_a"


def test_predominant_clock_tie_breaks_alphabetically() -> None:
    m = _populated_map(
        [
            _flop("top.u_a.f1", "clk_z"),
            _flop("top.u_a.f2", "clk_a"),
        ]
    )
    assert m.predominant_clock("top.u_a") == "clk_a"


def test_predominant_clock_returns_none_for_pure_comb() -> None:
    m = _populated_map([_flop("top.elsewhere.f1", "clk_a")])
    assert m.predominant_clock("top.u_a") is None


def test_predominant_clock_skips_untraceable_flops() -> None:
    m = _populated_map(
        [
            _flop("top.u_a.f1", None),
            _flop("top.u_a.f2", "clk_b"),
        ]
    )
    assert m.predominant_clock("top.u_a") == "clk_b"


def _crossing(
    *,
    src_clock: str,
    dst_clock: str,
    dst_flop: str,
    src_flop: str | None = None,
    async_per_sdc: bool = True,
) -> Crossing:
    return Crossing(
        src_clock=src_clock,
        dst_clock=dst_clock,
        dst_flop=dst_flop,
        min_hops=0,
        width=1,
        async_per_sdc=async_per_sdc,
        src_flop=src_flop or "top.src",
    )


def _map_with_crossings(crossings: list[Crossing]) -> DomainMap:
    return DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        crossings=tuple(crossings),
    )


# --- unit: crossings_into ----------------------------------------------------


def test_crossings_into_filters_by_dst_flop() -> None:
    a = _crossing(src_clock="clk_a", dst_clock="clk_b", dst_flop="top.u_x")
    b = _crossing(src_clock="clk_c", dst_clock="clk_d", dst_flop="top.u_y")
    m = _map_with_crossings([a, b])
    hits = m.crossings_into("top.u_x")
    assert len(hits) == 1
    assert hits[0].src_clock == "clk_a"


def test_crossings_into_filters_out_non_async_by_default() -> None:
    sync = _crossing(
        src_clock="clk_a",
        dst_clock="clk_a",
        dst_flop="top.u_x",
        async_per_sdc=False,
    )
    m = _map_with_crossings([sync])
    assert m.crossings_into("top.u_x") == ()
    assert m.crossings_into("top.u_x", async_only=False) == (sync,)


def test_predominant_clock_does_not_match_partial_prefix() -> None:
    # ``top.u_a`` must NOT match a flop at ``top.u_a_other.f1`` — the
    # prefix check has to be path-segment-aware via the trailing ".".
    m = _populated_map([_flop("top.u_a_other.f1", "clk_a")])
    assert m.predominant_clock("top.u_a") is None


# --- unit: tree renderer with synthetic graph -------------------------------


def _node(
    path: str,
    module: str,
    *,
    inst_name: str | None = None,
    children: tuple[HierNode, ...] = (),
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module,
            param_overrides=(),
            port_connections=(),
            location=None,
        )
        if inst_name is not None
        else None
    )
    return HierNode(
        instance_path=path,
        module_name=module,
        instance=inst,
        module=None,
        is_blackbox=False,
        children=children,
    )


def test_tree_renders_clock_suffix() -> None:
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    m = _populated_map([_flop("top.u_a.f1", "clk_x")])
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=m)
    assert buf.getvalue() == "top  [clk_x]\n└── u_a : child  [clk_x]\n"


def test_tree_marks_crossing_destinations() -> None:
    """Async crossings into a flop surface as ``⚠CDC[src→dst]`` suffix."""
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    m = _map_with_crossings(
        [_crossing(src_clock="clk_a", dst_clock="clk_b", dst_flop="top.u_dst")]
    )
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=m)
    output = buf.getvalue()
    assert "⚠CDC[clk_a→clk_b]" in output
    # Top has no crossing into it → no marker.
    assert output.splitlines()[0] == "top"


def test_tree_collapses_multiple_crossings_into_one_marker() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    m = _map_with_crossings(
        [
            _crossing(src_clock="clk_a", dst_clock="clk_b", dst_flop="top.u_dst"),
            _crossing(src_clock="clk_c", dst_clock="clk_b", dst_flop="top.u_dst"),
        ]
    )
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=m)
    # Both source clocks appear, alphabetically sorted, in one marker.
    assert "⚠CDC[clk_a→clk_b, clk_c→clk_b]" in buf.getvalue()


def test_tree_no_annotation_for_subtree_without_flops() -> None:
    pure_comb = _node("top.u_b", "comb", inst_name="u_b")
    root = _node("top", "top", children=(pure_comb,))
    m = _populated_map([_flop("top.u_a.f1", "clk_x")])
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=m)
    # u_b has no flops under it → renders without a suffix.
    assert buf.getvalue() == "top  [clk_x]\n└── u_b : comb\n"


def test_tree_empty_map_acts_like_no_map() -> None:
    """no-SDC payload should produce identical output to ``None``."""
    root = _node("top", "top")
    empty = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
    )
    buf_a, buf_b = io.StringIO(), io.StringIO()
    tree_render.render(root, buf_a, domain_map=None)
    tree_render.render(root, buf_b, domain_map=empty)
    assert buf_a.getvalue() == buf_b.getvalue()


# --- unit: dot renderer with synthetic graph --------------------------------


def test_dot_colors_node_when_clock_known() -> None:
    child = _node("top.u_a", "child")
    root = _node("top", "top", children=(child,))
    m = _populated_map([_flop("top.u_a.f1", "clk_x")])
    buf = io.StringIO()
    dot_render.render(root, buf, domain_map=m)
    text = buf.getvalue()
    assert "[clk_x]" in text  # appears in the node label
    # Same clock at root and child → same fillcolor on both lines.
    fill_for_top = _extract_fill(text, '"top" ')
    fill_for_child = _extract_fill(text, '"top.u_a" ')
    assert fill_for_top == fill_for_child
    assert fill_for_top.startswith("#")


def test_dot_styles_crossing_edges_in_red_dashed() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    m = _map_with_crossings(
        [_crossing(src_clock="clk_a", dst_clock="clk_b", dst_flop="top.u_dst")]
    )
    buf = io.StringIO()
    dot_render.render(root, buf, domain_map=m)
    output = buf.getvalue()
    # The edge to the crossing destination carries the warning label,
    # a red color, and dashed style — matches the conventions used
    # for other dashed-warning edges across the renderer family.
    assert "⚠CDC: clk_a→clk_b" in output
    assert "#dc2626" in output
    assert 'style="dashed"' in output
    # Non-destination edges aren't affected.
    assert output.count("#dc2626") == 2  # color + fontcolor on one edge


def test_dot_edge_label_combines_cdc_and_port_connections() -> None:
    """CDC marker prepends to the port-connection label, not replaces it."""
    inst = Instance(
        name="u_dst",
        module_name="ff",
        param_overrides=(),
        port_connections=(),
        location=None,
    )
    dst = HierNode(
        instance_path="top.u_dst",
        module_name="ff",
        instance=inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    # Build a port connection to test the combined label path.
    from rtl_buddy_view.extractor import PortConnection

    inst_with_conns = Instance(
        name="u_dst",
        module_name="ff",
        param_overrides=(),
        port_connections=(
            PortConnection(port_name="clk", net_expr_text="clk_b", location=None),
        ),
        location=None,
    )
    dst = HierNode(
        instance_path="top.u_dst",
        module_name="ff",
        instance=inst_with_conns,
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(dst,))
    m = _map_with_crossings(
        [_crossing(src_clock="clk_a", dst_clock="clk_b", dst_flop="top.u_dst")]
    )
    buf = io.StringIO()
    dot_render.render(root, buf, domain_map=m)
    output = buf.getvalue()
    assert "⚠CDC: clk_a→clk_b\\n.clk(clk_b)" in output


def test_dot_legend_lists_each_clock() -> None:
    root = _node("top", "top")
    m = _populated_map([_flop("top.f1", "clk_a")])
    buf = io.StringIO()
    dot_render.render(root, buf, domain_map=m, with_legend=True)
    text = buf.getvalue()
    assert "cluster_clock_legend" in text
    assert '"_legend_clk_a"' in text
    assert "Clocks" in text


def test_dot_legend_suppressed_when_map_empty() -> None:
    root = _node("top", "top")
    empty = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
    )
    buf = io.StringIO()
    dot_render.render(root, buf, domain_map=empty, with_legend=True)
    assert "cluster_clock_legend" not in buf.getvalue()


def _extract_fill(dot_text: str, node_prefix: str) -> str:
    """Pull the ``fillcolor=...`` value off the dot line for a node."""
    for line in dot_text.splitlines():
        if line.lstrip().startswith(node_prefix):
            marker = 'fillcolor="'
            idx = line.find(marker)
            if idx < 0:
                return "#f5f5f5"  # default
            start = idx + len(marker)
            end = line.find('"', start)
            return line[start:end]
    raise AssertionError(f"no node line starting with {node_prefix!r}")


# --- integration: real Verible parse + domain map ---------------------------


pytestmark_integration = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


@pytest.fixture
def integration_root() -> HierNode:
    table = parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )
    return build_hierarchy(table, "top")


@pytestmark_integration
def test_integration_tree_with_clock_suffixes(integration_root: HierNode) -> None:
    m = load_domain_map(FIXTURE_DIR / "domain_map.json")
    buf = io.StringIO()
    tree_render.render(integration_root, buf, domain_map=m)
    output = buf.getvalue()
    # u_fifo is mixed (one flop each clock) → alphabetical tie-break = clk_a
    assert "u_fifo : fifo  [clk_a]" in output
    # Leaf flops match exactly.
    assert "u_wr_ptr : ff  [clk_a]" in output
    assert "u_rd_ptr : ff  [clk_b]" in output


@pytestmark_integration
def test_integration_dot_with_legend(integration_root: HierNode) -> None:
    m = load_domain_map(FIXTURE_DIR / "domain_map.json")
    buf = io.StringIO()
    dot_render.render(integration_root, buf, domain_map=m, with_legend=True)
    output = buf.getvalue()
    assert "[clk_a]" in output
    assert "[clk_b]" in output
    assert "cluster_clock_legend" in output
    # Both flop nodes get a colored fill (not the default gray).
    assert "#f5f5f5" not in _extract_fill(output, '"top.u_fifo.u_wr_ptr" ')
    # u_rd_ptr is the destination of an async crossing → red dashed edge.
    rd_edge = next(
        line
        for line in output.splitlines()
        if "top.u_fifo.u_rd_ptr" in line and "->" in line
    )
    assert "⚠CDC: clk_a→clk_b" in rd_edge
    assert "#dc2626" in rd_edge


@pytestmark_integration
def test_integration_tree_marks_cdc(integration_root: HierNode) -> None:
    m = load_domain_map(FIXTURE_DIR / "domain_map.json")
    buf = io.StringIO()
    tree_render.render(integration_root, buf, domain_map=m)
    output = buf.getvalue()
    assert "u_rd_ptr : ff  [clk_b]  ⚠CDC[clk_a→clk_b]" in output
    # u_wr_ptr is the *source* of the crossing, not the dest — no marker.
    assert "u_wr_ptr : ff  [clk_a]\n" in output


@pytestmark_integration
def test_cli_passes_annotations_through() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "top",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--format",
            "tree",
            "--cdc-annotations",
            str(FIXTURE_DIR / "domain_map.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "[clk_a]" in result.stdout
    assert "[clk_b]" in result.stdout


@pytestmark_integration
def test_cli_clock_legend_emits_subgraph() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "top",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--format",
            "dot",
            "--cdc-annotations",
            str(FIXTURE_DIR / "domain_map.json"),
            "--clock-legend",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "cluster_clock_legend" in result.stdout
