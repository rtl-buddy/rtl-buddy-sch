"""Unit tests for the Graphviz dot renderer.

These tests build :class:`HierNode` instances directly so the
renderer can be exercised without spawning Verible. The end-to-end
fixture-driven coverage lives in ``test_parameterized_fifo.py`` and
the existing counter / empty-module tests.
"""

from __future__ import annotations

import io

from rtl_buddy_view.extractor import Instance, ParameterOverride, PortConnection
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render import dot as dot_render


def _node(
    instance_path: str,
    module_name: str,
    *,
    instance: Instance | None = None,
    is_blackbox: bool = False,
    children: tuple[HierNode, ...] = (),
) -> HierNode:
    return HierNode(
        instance_path=instance_path,
        module_name=module_name,
        instance=instance,
        module=None,
        is_blackbox=is_blackbox,
        children=children,
    )


def _instance(
    name: str,
    module_name: str,
    *,
    overrides: tuple[ParameterOverride, ...] = (),
    connections: tuple[PortConnection, ...] = (),
) -> Instance:
    return Instance(
        name=name,
        module_name=module_name,
        param_overrides=overrides,
        port_connections=connections,
        location=None,
    )


def _conn(port_name: str | None, net: str) -> PortConnection:
    return PortConnection(port_name=port_name, net_expr_text=net, location=None)


def test_top_renders_as_titled_cluster() -> None:
    """Top module is the frame, not a regular node."""
    out = io.StringIO()
    dot_render.render(_node("top", "top"), out)
    text = out.getvalue()
    assert text.startswith("digraph hierarchy {\n")
    assert "subgraph cluster_top {" in text
    assert 'label="top"' in text
    # Frame uses outline only — no fillcolor on the cluster itself
    # (per-child clock fills carry the cue when annotations are on).
    assert 'fillcolor="' not in text.split("cluster_top")[1].split("}")[0]
    assert text.rstrip().endswith("}")


def test_mid_to_leaf_edge_survives_frame_mode() -> None:
    """Frame mode drops top→child edges but keeps every deeper edge."""
    leaf = _node("top.u_mid.u_leaf", "leaf", instance=_instance("u_leaf", "leaf"))
    mid = _node(
        "top.u_mid",
        "mid",
        instance=_instance("u_mid", "mid"),
        children=(leaf,),
    )
    root = _node("top", "top", children=(mid,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"top.u_mid" -> "top.u_mid.u_leaf"' in text
    # Node label lines stack via ``\l`` (left-align) under monospace.
    assert "u_leaf\\lleaf" in text
    # No top→mid edge — that's containment, not a drawn arrow.
    assert '"top" -> "top.u_mid"' not in text


def test_blackbox_uses_dashed_style() -> None:
    bb = _node(
        "top.u_x",
        "missing",
        instance=_instance("u_x", "missing"),
        is_blackbox=True,
    )
    root = _node("top", "top", children=(bb,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert "dashed" in text
    assert "(blackbox)" in text


def test_param_overrides_in_label() -> None:
    """Param overrides go on the child's node label, one per line,
    with ``.NAME`` left-padded so the ``(`` parens form a column.
    """
    inst = _instance(
        "u_core",
        "core",
        overrides=(
            ParameterOverride(param_name="WIDTH", value_text="16", location=None),
            ParameterOverride(param_name="DEPTH", value_text="32", location=None),
        ),
    )
    child = _node("top.u_core", "core", instance=inst, is_blackbox=True)
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # Both names are 5 chars — no padding needed; parens already
    # align column-wise.
    assert r"#(\l  .WIDTH(16)\l  .DEPTH(32)\l)" in text


def test_param_overrides_pad_to_align_parens() -> None:
    """Mixed-width param names get right-padded with spaces so the
    opening ``(`` column-aligns under the monospace font.
    """
    inst = _instance(
        "u_x",
        "core",
        overrides=(
            ParameterOverride(param_name="W", value_text="1", location=None),
            ParameterOverride(param_name="MAX_DEPTH", value_text="32", location=None),
        ),
    )
    child = _node("top.u_x", "core", instance=inst, is_blackbox=True)
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # ``W`` padded with 8 spaces to align with ``MAX_DEPTH`` (9 chars).
    assert r"  .W        (1)\l  .MAX_DEPTH(32)\l" in text


def test_graph_uses_monospace_fontname() -> None:
    """Graph + node + edge scopes all set ``fontname=...,monospace`` so
    space-padding actually visually aligns. The padding logic only
    works under a fixed-width font."""
    out = io.StringIO()
    dot_render.render(_node("top", "top"), out)
    text = out.getvalue()
    assert "monospace" in text.split("subgraph")[0]  # graph-level
    assert "node [" in text and "monospace" in text


def test_label_escapes_quotes_and_backslashes() -> None:
    inst = _instance(
        "u_x",
        "x",
        overrides=(
            ParameterOverride(
                param_name="MSG",
                value_text='"hi"',  # Includes quote characters
                location=None,
            ),
        ),
    )
    child = _node("top.u_x", "x", instance=inst, is_blackbox=True)
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # Both quotes must be backslash-escaped inside the dot label.
    assert r"\"hi\"" in text


def test_edge_label_lists_port_connections() -> None:
    """Port-connection labels appear on edges below the top frame.

    Top→child edges don't exist in frame mode, so this test uses a
    mid→leaf edge to pin the label format.
    """
    leaf_inst = _instance(
        "u_ff", "ff", connections=(_conn("clk", "clk"), _conn("q", "q[0]"))
    )
    leaf = _node("top.u_mid.u_ff", "ff", instance=leaf_inst)
    mid = _node(
        "top.u_mid",
        "mid",
        instance=_instance("u_mid", "mid"),
        children=(leaf,),
    )
    root = _node("top", "top", children=(mid,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # Port-name padded so ``(`` parens align under the monospace
    # font: ``clk`` is the longest at 3 chars, ``q`` gets 2-char pad.
    assert r'"top.u_mid" -> "top.u_mid.u_ff" [label=".clk(clk)\l.q  (q[0])\l"];' in text


def test_edge_label_truncates_long_port_lists() -> None:
    conns = tuple(_conn(f"p{i}", f"n{i}") for i in range(20))
    leaf = _node(
        "top.u_mid.u", "wide", instance=_instance("u", "wide", connections=conns)
    )
    mid = _node(
        "top.u_mid",
        "mid",
        instance=_instance("u_mid", "mid"),
        children=(leaf,),
    )
    root = _node("top", "top", children=(mid,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # First few connections appear; later ones are summarised.
    assert ".p0(n0)" in text
    assert ".p1(n1)" in text
    assert "(+" in text and " more)" in text
    assert ".p19(n19)" not in text


def test_no_edge_label_when_no_connections() -> None:
    leaf = _node("top.u_mid.u", "child", instance=_instance("u", "child"))
    mid = _node(
        "top.u_mid",
        "mid",
        instance=_instance("u_mid", "mid"),
        children=(leaf,),
    )
    root = _node("top", "top", children=(mid,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"top.u_mid" -> "top.u_mid.u";' in text
    # No label= attribute on this edge.
    assert '"top.u_mid" -> "top.u_mid.u" [' not in text


# --- multi-clock label + two-tone + HTML grid -------------------------------


def _top_with_ports(
    *ports: tuple[str, str], children: tuple[HierNode, ...] = ()
) -> HierNode:
    from rtl_buddy_view.extractor import Module, Port

    mod = Module(
        name="top",
        ports=tuple(
            Port(name=n, direction=d, type_text=None, location=None) for n, d in ports
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    return HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=mod,
        is_blackbox=False,
        children=children,
    )


def test_clock_label_lists_all_clocks_in_subtree() -> None:
    """Multi-clock subtrees label with every distinct clock, not just
    the predominant one. ``predominant_clock`` would silently flatten a
    multi-domain module to its majority — misleading for any CDC view.
    """
    from rtl_buddy_view.annotations import Clock, DomainMap, FlopDomain

    sync_inst = _instance("u_sync", "ip_cdc_sync")
    sync = _node("top.u_sync", "ip_cdc_sync", instance=sync_inst)
    root = _top_with_ports(children=(sync,))
    m = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(
            Clock(name="clk_a", period=10.0, source="create_clock", ports=()),
            Clock(name="clk_b", period=8.0, source="create_clock", ports=()),
        ),
        flop_domains=(
            FlopDomain(instance_path="top.u_sync.f1", clock="clk_a", location=None),
            FlopDomain(instance_path="top.u_sync.f2", clock="clk_b", location=None),
        ),
    )
    out = io.StringIO()
    dot_render.render(root, out, domain_map=m)
    text = out.getvalue()
    assert "[clk_a, clk_b]" in text


def test_two_tone_fill_for_single_direction_crossing() -> None:
    """A node with one unambiguous (src, dst) async crossing gets a
    ``style="rounded,striped"`` two-tone fill — left half src color,
    right half dst color."""
    from rtl_buddy_view.annotations import Clock, Crossing, DomainMap, FlopDomain

    sync_inst = _instance("u_sync", "ip_cdc_sync")
    sync = _node("top.u_sync", "ip_cdc_sync", instance=sync_inst)
    root = _top_with_ports(("clk_a", "input"), children=(sync,))
    m = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(
            Clock(name="clk_a", period=10.0, source="create_clock", ports=("clk_a",)),
            Clock(name="clk_b", period=8.0, source="create_clock", ports=()),
        ),
        flop_domains=(
            FlopDomain(instance_path="top.u_sync.f1", clock="clk_b", location=None),
        ),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_sync.f1",
                dst_source_instance_path="top.u_sync",
                min_hops=0,
                width=1,
                async_per_sdc=True,
            ),
        ),
    )
    out = io.StringIO()
    dot_render.render(root, out, domain_map=m)
    text = out.getvalue()
    line = [ln for ln in text.splitlines() if '"top.u_sync"' in ln][0]
    assert 'style="rounded,striped"' in line
    # Two palette colors separated by ``:`` (the dot stripe syntax).
    fill = line.split("fillcolor=")[1].split("]")[0]
    assert ":" in fill


def test_html_grid_for_bidirectional_crossings() -> None:
    """Multiple distinct (src, dst) pairs → HTML-table label with one
    row per direction (left=src color, right=dst color). Striped style
    can't express the 2-row case."""
    from rtl_buddy_view.annotations import Clock, Crossing, DomainMap, FlopDomain

    fifo_inst = _instance("u_fifo", "ip_cdc_fifo")
    fifo = _node("top.u_fifo", "ip_cdc_fifo", instance=fifo_inst)
    root = _top_with_ports(children=(fifo,))
    m = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(
            Clock(name="clk_a", period=10.0, source="create_clock", ports=()),
            Clock(name="clk_b", period=8.0, source="create_clock", ports=()),
        ),
        flop_domains=(
            FlopDomain(instance_path="top.u_fifo.f1", clock="clk_a", location=None),
            FlopDomain(instance_path="top.u_fifo.f2", clock="clk_b", location=None),
        ),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_fifo.f1",
                dst_source_instance_path="top.u_fifo",
                min_hops=0,
                width=1,
                async_per_sdc=True,
            ),
            Crossing(
                src_clock="clk_b",
                dst_clock="clk_a",
                dst_flop="top.u_fifo.f2",
                dst_source_instance_path="top.u_fifo",
                min_hops=0,
                width=1,
                async_per_sdc=True,
            ),
        ),
    )
    out = io.StringIO()
    dot_render.render(root, out, domain_map=m)
    text = out.getvalue()
    line = [ln for ln in text.splitlines() if '"top.u_fifo"' in ln][0]
    assert "<TABLE" in line
    assert "shape=plaintext" in line
    # Two crossing rows × two BGCOLOR cells = 4.
    assert line.count("BGCOLOR") == 4
