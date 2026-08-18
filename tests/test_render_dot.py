"""Unit tests for the Graphviz dot renderer.

These tests build :class:`HierNode` instances directly so the
renderer can be exercised without spawning Verible. The end-to-end
fixture-driven coverage lives in ``test_parameterized_fifo.py`` and
the existing counter / empty-module tests.
"""

from __future__ import annotations

import io

from rtl_buddy_view.extractor import (
    Instance,
    ModuleTable,
    ParameterOverride,
    PortConnection,
)
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


# --- shared helper for port-anchor tests ------------------------------------


def _top_with_ports(
    *ports: tuple[str, str], children: tuple[HierNode, ...] = ()
) -> HierNode:
    """Build a top HierNode whose Module carries the given ports.

    Each entry is ``(name, direction)``.
    """
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


# --- multi-clock label + two-tone + HTML grid -------------------------------


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


# --- port → child signal-flow edges -----------------------------------------


def test_signal_edge_connects_bare_input_to_direct_child() -> None:
    """A child's ``.port(net)`` with ``net`` matching a top input port
    name yields a thin edge from the input anchor to the child."""
    inst = _instance("u_a", "child", connections=(_conn("d", "data_in"),))
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(
        ("data_in", "input"),
        ("out0", "output"),
        children=(child,),
    )
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"_in_data_in" -> "top.u_a"' in text


def test_signal_edge_connects_child_to_bare_output() -> None:
    inst = _instance("u_a", "child", connections=(_conn("q", "out0"),))
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(
        ("data_in", "input"),
        ("out0", "output"),
        children=(child,),
    )
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"top.u_a" -> "_out_out0"' in text


def test_signal_edge_skips_clock_ports_by_name() -> None:
    """Without a domain map: clock-looking port names (``clk`` token)
    are dropped — otherwise every flop in the design draws a line to
    the clock and the diagram becomes a spiderweb."""
    inst = _instance(
        "u_a", "child", connections=(_conn("clk", "a_clk"), _conn("d", "data_in"))
    )
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(
        ("a_clk", "input"),
        ("data_in", "input"),
        children=(child,),
    )
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"_in_a_clk" -> "top.u_a"' not in text
    # Data port still wired.
    assert '"_in_data_in" -> "top.u_a"' in text


def test_signal_edge_skips_reset_ports_by_name() -> None:
    inst = _instance(
        "u_a", "child", connections=(_conn("rst_n", "s_rst_n"), _conn("d", "data_in"))
    )
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(
        ("s_rst_n", "input"),
        ("data_in", "input"),
        children=(child,),
    )
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"_in_s_rst_n" -> "top.u_a"' not in text
    assert '"_in_data_in" -> "top.u_a"' in text


def test_signal_edge_skips_complex_net_expressions() -> None:
    """Slices, concatenations, and arbitrary expressions are not net-
    traced — only bare identifiers count as port references."""
    inst = _instance(
        "u_a",
        "child",
        connections=(
            _conn("a", "data_in[7:0]"),
            _conn("b", "{x, y}"),
            _conn("c", "data_in & mask"),
        ),
    )
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(("data_in", "input"), children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"_in_data_in" -> "top.u_a"' not in text


def test_signal_edge_dedupes_same_port_multiple_connections() -> None:
    """Two ``.portA(d), .portB(d)`` connections to the same top port
    yield a single edge, not two parallel ones."""
    inst = _instance(
        "u_a",
        "child",
        connections=(_conn("a", "data_in"), _conn("a_alias", "data_in")),
    )
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(("data_in", "input"), children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert text.count('"_in_data_in" -> "top.u_a"') == 1


def test_signal_edge_uses_domain_map_clocks_for_filter() -> None:
    """A name that isn't ``clk``-token-shaped but is named as a clock
    in the domain map is still filtered."""
    from rtl_buddy_view.annotations import Clock, DomainMap

    inst = _instance(
        "u_a", "child", connections=(_conn("c", "freerun"), _conn("d", "data_in"))
    )
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(
        ("freerun", "input"),
        ("data_in", "input"),
        children=(child,),
    )
    m = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(
            Clock(
                name="freerun", period=10.0, source="create_clock", ports=("freerun",)
            ),
        ),
    )
    out = io.StringIO()
    dot_render.render(root, out, domain_map=m)
    text = out.getvalue()
    assert '"_in_freerun" -> "top.u_a"' not in text
    assert '"_in_data_in" -> "top.u_a"' in text


def test_signal_edge_colored_by_explicit_port_clock() -> None:
    """``port_domains[port=X, clock=clk_a]`` colors the edge with the
    palette swatch for ``clk_a``."""
    from rtl_buddy_view.annotations import Clock, DomainMap, PortDomain

    inst = _instance("u_a", "child", connections=(_conn("d", "data_in"),))
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(("data_in", "input"), children=(child,))
    m = DomainMap(
        schema_version="1.0",
        generator_name="t",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(
            Clock(name="clk_a", period=10.0, source="create_clock", ports=("clk_a",)),
        ),
        port_domains=(
            PortDomain(module="top", port="data_in", clock="clk_a", kind="input"),
        ),
    )
    out = io.StringIO()
    dot_render.render(root, out, domain_map=m)
    text = out.getvalue()
    # Edge exists AND uses a non-neutral palette color (the slate
    # fallback would be ``#cbd5e1``; any other ``#`` hex is a
    # palette swatch).
    edge_line = [ln for ln in text.splitlines() if '"_in_data_in" -> "top.u_a"' in ln][
        0
    ]
    assert 'color="#cbd5e1"' not in edge_line
    assert 'color="#' in edge_line


# --- block-diagram mode -----------------------------------------------------


def _blk_fixture() -> tuple[HierNode, ModuleTable]:
    """``top`` with a clustered producer feeding a leaf consumer.

    Shape mirrors ``tests/fixtures/block_diagram_demo`` so the
    synthetic and the Verible-backed tests assert the same thing:

        _in_din ─▶ u_prod (cluster, holds u_stage) ─▶ u_cons ─▶ _out_dout
    """
    from rtl_buddy_view.extractor import Module, Port

    def port(name: str, direction: str | None) -> Port:
        return Port(
            name=name,
            direction=direction,  # type: ignore[arg-type]
            type_text=None,
            location=None,
        )

    def conn(p: str, net: str) -> PortConnection:
        return PortConnection(port_name=p, net_expr_text=net, location=None)

    leaf_mod = Module(
        name="leaf",
        ports=(port("din", "input"), port("dout", "output")),
        parameters=(),
        instances=(),
        location=None,
    )
    prod_mod = Module(
        name="prod",
        ports=(port("cmd", "input"), port("payload", "output")),
        parameters=(),
        instances=(
            Instance(
                name="u_stage",
                module_name="leaf",
                param_overrides=(),
                port_connections=(conn("din", "cmd"), conn("dout", "payload")),
                location=None,
            ),
        ),
        location=None,
    )
    cons_mod = Module(
        name="cons",
        ports=(port("payload", "input"), port("result", "output")),
        parameters=(),
        instances=(),
        location=None,
    )
    top_mod = Module(
        name="top",
        ports=(port("din", "input"), port("dout", "output")),
        parameters=(),
        instances=(
            Instance(
                name="u_prod",
                module_name="prod",
                param_overrides=(),
                port_connections=(conn("cmd", "din"), conn("payload", "w")),
                location=None,
            ),
            Instance(
                name="u_cons",
                module_name="cons",
                param_overrides=(),
                port_connections=(conn("payload", "w"), conn("result", "dout")),
                location=None,
            ),
        ),
        location=None,
    )
    table = ModuleTable(
        modules_by_name={m.name: m for m in (top_mod, prod_mod, cons_mod, leaf_mod)}
    )

    stage = HierNode(
        instance_path="top.u_prod.u_stage",
        module_name="leaf",
        instance=prod_mod.instances[0],
        module=leaf_mod,
        is_blackbox=False,
    )
    prod = HierNode(
        instance_path="top.u_prod",
        module_name="prod",
        instance=top_mod.instances[0],
        module=prod_mod,
        is_blackbox=False,
        children=(stage,),
    )
    cons = HierNode(
        instance_path="top.u_cons",
        module_name="cons",
        instance=top_mod.instances[1],
        module=cons_mod,
        is_blackbox=False,
    )
    top = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=top_mod,
        is_blackbox=False,
        children=(prod, cons),
    )
    return top, table


def test_block_mode_implies_cluster_layout() -> None:
    top, table = _blk_fixture()
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True, module_table=table)
    text = out.getvalue()
    assert "subgraph cluster_top_u_prod {" in text
    # The leaf inside the cluster is a plain node, not a nested cluster.
    assert "subgraph cluster_top_u_cons {" not in text


def test_block_mode_emits_connectivity_edges_with_xlabel() -> None:
    """Sibling dataflow, labelled via ``xlabel`` — ortho splines can't
    place a regular edge label and Graphviz warns when asked to."""
    top, table = _blk_fixture()
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True, module_table=table)
    text = out.getvalue()
    line = [ln for ln in text.splitlines() if '"top.u_prod" -> "top.u_cons"' in ln][0]
    assert 'xlabel="w\\l"' in line
    assert 'color="#475569"' in line
    assert "penwidth=1.0" in line
    assert "arrowsize=0.7" in line
    # ``label=`` would be the warning-triggering attribute.
    assert ", label=" not in line
    # The clustered source clips at its border.
    assert 'ltail="cluster_top_u_prod"' in line


def test_block_mode_wires_top_port_anchors() -> None:
    top, table = _blk_fixture()
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True, module_table=table)
    text = out.getvalue()
    assert '"_in_din" -> "top.u_prod"' in text
    assert '"top.u_cons" -> "_out_dout"' in text


def test_block_mode_drops_port_map_edge_labels() -> None:
    """The parent→child port-map edges are the thing block mode replaces."""
    top, table = _blk_fixture()
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True, module_table=table)
    text = out.getvalue()
    assert '"top" -> "top.u_prod"' not in text
    assert '"top.u_prod" -> "top.u_prod.u_stage"' not in text
    assert ".payload(" not in text


def test_block_mode_skips_port_endpoints_below_the_top_frame() -> None:
    """A nested scope's ports are represented by its cluster border,
    so they must never sprout their own anchors or edges."""
    top, table = _blk_fixture()
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True, module_table=table)
    text = out.getvalue()
    assert '"_in_cmd"' not in text
    assert '"_out_payload"' not in text


def test_block_mode_without_module_table_degrades_to_cluster_tree() -> None:
    top, _ = _blk_fixture()
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True)
    text = out.getvalue()
    assert "subgraph cluster_top_u_prod {" in text
    assert 'color="#475569"' not in text


def test_block_mode_is_deterministic() -> None:
    top, table = _blk_fixture()
    first, second = io.StringIO(), io.StringIO()
    dot_render.render(top, first, block_diagram=True, module_table=table)
    dot_render.render(top, second, block_diagram=True, module_table=table)
    assert first.getvalue() == second.getvalue()


def test_block_mode_with_no_domain_map_matches_the_unannotated_case() -> None:
    """Graceful degradation: an empty overlay changes nothing."""
    from rtl_buddy_view.annotations import DomainMap

    top, table = _blk_fixture()
    plain, empty = io.StringIO(), io.StringIO()
    dot_render.render(top, plain, block_diagram=True, module_table=table)
    dot_render.render(
        top,
        empty,
        block_diagram=True,
        module_table=table,
        domain_map=DomainMap(
            schema_version="1.0",
            generator_name="x",
            generator_version="0",
            design_top="top",
            design_frontend="slang",
        ),
    )
    assert plain.getvalue() == empty.getvalue()


def test_block_mode_bundles_interface_ports_into_one_anchor() -> None:
    """A block diagram's terminal for a bus is the bus, not its wires."""
    from rtl_buddy_view.extractor import Interface, Modport, Module, Port

    iface = Interface(
        name="bus_if",
        parameters=(),
        signals=(),
        modports=(Modport(name="sub", inputs=("sel",), outputs=("rdata",)),),
        location=None,
    )
    sub_mod = Module(
        name="sub",
        ports=(Port(name="sel", direction="input", type_text=None, location=None),),
        parameters=(),
        instances=(),
        location=None,
    )
    top_mod = Module(
        name="top",
        ports=(
            Port(
                name="bus",
                direction=None,
                type_text=None,
                location=None,
                port_kind="interface",
                interface_type="bus_if",
                modport="sub",
            ),
        ),
        parameters=(),
        instances=(
            Instance(
                name="u_sub",
                module_name="sub",
                param_overrides=(),
                port_connections=(
                    PortConnection(
                        port_name="sel", net_expr_text="bus.sel", location=None
                    ),
                ),
                location=None,
            ),
        ),
        location=None,
    )
    table = ModuleTable(
        modules_by_name={"top": top_mod, "sub": sub_mod},
        interfaces_by_name={"bus_if": iface},
    )
    child = HierNode(
        instance_path="top.u_sub",
        module_name="sub",
        instance=top_mod.instances[0],
        module=sub_mod,
        is_blackbox=False,
    )
    top = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=top_mod,
        is_blackbox=False,
        children=(child,),
    )
    out = io.StringIO()
    dot_render.render(top, out, block_diagram=True, module_table=table)
    text = out.getvalue()
    assert '"_in_bus"' in text
    # Not flattened into per-signal anchors.
    assert '"_in_bus.sel"' not in text
    assert '"_in_bus" -> "top.u_sub"' in text


# --- lhead only when the cluster actually exists -----------------------------


def test_default_box_mode_omits_lhead_for_non_leaf_children() -> None:
    """The bug: ``lhead`` named a cluster the box layout never emitted,
    so Graphviz warned ``lhead cluster_… not found`` on every render."""
    grand = _node("top.u_a.u_b", "grand", instance=_instance("u_b", "grand"))
    child = _node(
        "top.u_a",
        "child",
        instance=_instance("u_a", "child", connections=(_conn("d", "data_in"),)),
        children=(grand,),
    )
    root = _top_with_ports(("data_in", "input"), children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"_in_data_in" -> "top.u_a"' in text
    assert "lhead=" not in text
    assert "subgraph cluster_top_u_a" not in text


def test_cluster_mode_keeps_lhead_for_non_leaf_children() -> None:
    grand = _node("top.u_a.u_b", "grand", instance=_instance("u_b", "grand"))
    child = _node(
        "top.u_a",
        "child",
        instance=_instance("u_a", "child", connections=(_conn("d", "data_in"),)),
        children=(grand,),
    )
    root = _top_with_ports(("data_in", "input"), children=(child,))
    out = io.StringIO()
    dot_render.render(root, out, as_cluster_tree=True)
    text = out.getvalue()
    assert 'lhead="cluster_top_u_a"' in text


def test_signal_edge_traces_implicit_named_connections() -> None:
    """``.data_in`` shorthand binds to the same-named net; the pre-fix
    renderer dropped every signal wired that way."""
    inst = _instance("u_a", "child", connections=(_conn("data_in", ""),))
    child = _node("top.u_a", "child", instance=inst)
    root = _top_with_ports(("data_in", "input"), children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"_in_data_in" -> "top.u_a"' in text
