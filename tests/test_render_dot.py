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


def test_renders_single_node() -> None:
    out = io.StringIO()
    dot_render.render(_node("top", "top"), out)
    text = out.getvalue()
    assert text.startswith("digraph hierarchy {\n")
    assert '"top" [label="top\\ntop"];' in text
    assert text.rstrip().endswith("}")


def test_renders_parent_child_edge() -> None:
    child = _node("top.u_child", "child", instance=_instance("u_child", "child"))
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"top" -> "top.u_child";' in text
    assert '"u_child\\nchild"' in text


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
    assert "#(.WIDTH(16), .DEPTH(32))" in text


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
    inst = _instance(
        "u_ff",
        "ff",
        connections=(_conn("clk", "clk"), _conn("q", "q[0]")),
    )
    child = _node("top.u_ff", "ff", instance=inst)
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # Multiline label: ``\l`` is Graphviz's left-aligned newline.
    # One port-connection per line, trailing ``\l`` anchors the last
    # line to the left margin.
    assert r'"top" -> "top.u_ff" [label=".clk(clk)\l.q(q[0])\l"];' in text


def test_edge_label_truncates_long_port_lists() -> None:
    conns = tuple(_conn(f"p{i}", f"n{i}") for i in range(20))
    inst = _instance("u", "wide", connections=conns)
    child = _node("top.u", "wide", instance=inst)
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    # First few connections appear; later ones are summarised.
    assert ".p0(n0)" in text
    assert ".p1(n1)" in text
    # Last few that fit before the cap should be there; everything
    # beyond is rolled into the "(+N more)" overflow note.
    assert "(+" in text and " more)" in text
    # The very last connection (.p19) should NOT appear inline.
    assert ".p19(n19)" not in text


def test_no_edge_label_when_no_connections() -> None:
    inst = _instance("u", "child")
    child = _node("top.u", "child", instance=inst)
    root = _node("top", "top", children=(child,))
    out = io.StringIO()
    dot_render.render(root, out)
    text = out.getvalue()
    assert '"top" -> "top.u";' in text
    # No label= attribute on this edge.
    assert '"top" -> "top.u" [' not in text
