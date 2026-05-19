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
    assert "u_leaf\\nleaf" in text
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
    """Param overrides go on the child's node label, one per line."""
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
    # Multi-line format — each override on its own ``\l``-aligned
    # line, two-space indented inside the ``#( … )`` block.
    assert r"#(\l  .WIDTH(16)\l  .DEPTH(32)\l)" in text


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
    assert r'"top.u_mid" -> "top.u_mid.u_ff" [label=".clk(clk)\l.q(q[0])\l"];' in text


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


# --- port → child signal-flow edges -----------------------------------------


def _top_with_ports(
    *ports: tuple[str, str], children: tuple[HierNode, ...] = ()
) -> HierNode:
    """Helper: build a top HierNode whose Module carries the given ports.

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
