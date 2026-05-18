"""Tests for the mermaid renderer.

Synthetic HierNodes — no Verible required. The end-to-end coverage
against the two_clock_design fixture lives in
``test_clock_overlay.py``; here we sit on the renderer's specific
mermaid-syntax decisions (slugging, escapes, dashed-arrow on CDC).
"""

from __future__ import annotations

import io

from rtl_buddy_view.annotations import (
    Clock,
    Crossing,
    DomainMap,
    FlopDomain,
)
from rtl_buddy_view.extractor import Instance, ParameterOverride, PortConnection
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render import mermaid as mermaid_render


def _node(
    path: str,
    module: str,
    *,
    inst_name: str | None = None,
    is_blackbox: bool = False,
    children: tuple[HierNode, ...] = (),
    overrides: tuple[ParameterOverride, ...] = (),
    connections: tuple[PortConnection, ...] = (),
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module,
            param_overrides=overrides,
            port_connections=connections,
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
        is_blackbox=is_blackbox,
        children=children,
    )


def _populated_map(
    flops: list[FlopDomain] = (), crossings: list[Crossing] = ()
) -> DomainMap:
    return DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        flop_domains=tuple(flops),
        crossings=tuple(crossings),
    )


def test_wraps_output_in_mermaid_fence() -> None:
    buf = io.StringIO()
    mermaid_render.render(_node("top", "top"), buf)
    text = buf.getvalue()
    assert text.startswith("```mermaid\n")
    assert text.rstrip().endswith("```")
    assert "flowchart TB" in text


def test_slugs_dots_in_instance_paths() -> None:
    child = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    buf = io.StringIO()
    mermaid_render.render(root, buf)
    text = buf.getvalue()
    # IDs use underscores; verbatim path appears inside the label
    # via the inst_name + module_name combination.
    assert "top_u_a[" in text
    assert "top --> top_u_a" in text


def test_blackbox_node_has_dashed_stroke() -> None:
    bb = _node("top.u_x", "missing", inst_name="u_x", is_blackbox=True)
    root = _node("top", "top", children=(bb,))
    buf = io.StringIO()
    mermaid_render.render(root, buf)
    text = buf.getvalue()
    assert "stroke-dasharray: 3 3" in text
    assert "(blackbox)" in text


def test_cdc_crossing_uses_dashed_arrow_and_red_stroke() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    m = _populated_map(
        crossings=[
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_dst",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="top.src",
            )
        ]
    )
    buf = io.StringIO()
    mermaid_render.render(root, buf, domain_map=m)
    text = buf.getvalue()
    # mermaid's dashed-arrow syntax for crossing edges.
    assert "top -.->" in text
    # Crossing label appears with the warning glyph.
    assert "⚠CDC: clk_a→clk_b" in text
    # Destination node gets a red stroke.
    assert "stroke:#dc2626" in text


def test_html_special_chars_in_labels_are_escaped() -> None:
    inst = Instance(
        name="u_dst",
        module_name="ff",
        param_overrides=(
            ParameterOverride(
                param_name="MSG",
                value_text='"x>y"',
                location=None,
            ),
        ),
        port_connections=(),
        location=None,
    )
    child = HierNode(
        instance_path="top.u_dst",
        module_name="ff",
        instance=inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = _node("top", "top", children=(child,))
    buf = io.StringIO()
    mermaid_render.render(root, buf)
    text = buf.getvalue()
    # HTML-encode quotes (mermaid label syntax doesn't take backslash
    # escapes) and angle brackets so the parser doesn't misread the
    # label as nested HTML.
    assert "&quot;x&gt;y&quot;" in text


def test_empty_map_acts_like_no_map() -> None:
    root = _node("top", "top")
    empty = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
    )
    buf_a, buf_b = io.StringIO(), io.StringIO()
    mermaid_render.render(root, buf_a, domain_map=None)
    mermaid_render.render(root, buf_b, domain_map=empty)
    assert buf_a.getvalue() == buf_b.getvalue()
