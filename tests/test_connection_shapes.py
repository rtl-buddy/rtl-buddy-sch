"""End-to-end tests for positional and shorthand connection forms.

Three instances in the fixture exercise the three shapes the
elaborator can produce: full named, positional, and implicit
``.port`` shorthand. The CST walker maps each to a consistent
:class:`PortConnection` / :class:`ParameterOverride` shape so
renderers and the query API don't need to special-case them.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "connection_shapes"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _instances():
    table = parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )
    return {i.name: i for i in table.modules_by_name["top"].instances}


def test_named_connections_and_overrides() -> None:
    inst = _instances()["u_named"]
    assert [(o.param_name, o.value_text) for o in inst.param_overrides] == [
        ("WIDTH", "16"),
        ("DEPTH", "32"),
    ]
    assert [(c.port_name, c.net_expr_text) for c in inst.port_connections] == [
        ("clk", "clk"),
        ("rst_n", "rst_n"),
        ("q", "q"),
    ]


def test_positional_connections_and_overrides() -> None:
    inst = _instances()["u_pos"]
    # Positional overrides: param_name is None, order preserved.
    assert [(o.param_name, o.value_text) for o in inst.param_overrides] == [
        (None, "16"),
        (None, "32"),
    ]
    # Positional connections: port_name is None, net expression kept.
    assert [(c.port_name, c.net_expr_text) for c in inst.port_connections] == [
        (None, "clk"),
        (None, "rst_n"),
        (None, "q"),
    ]


def test_implicit_shorthand_connections() -> None:
    inst = _instances()["u_short"]
    # Implicit `.port` shorthand: port_name set, net_expr_text empty
    # so renderers know to emit `.port` instead of `.port(port)`.
    assert [(c.port_name, c.net_expr_text) for c in inst.port_connections] == [
        ("clk", ""),
        ("rst_n", ""),
        ("q", ""),
    ]


def test_dot_renders_each_shape_distinctively() -> None:
    table = parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )
    root = build_hierarchy(table, "top")
    buf = io.StringIO()
    dot_render.render(root, buf)
    output = buf.getvalue()
    # Frame mode: top→child edges don't exist (containment is the
    # relationship); the per-edge port-connection formatting for
    # named/positional/shorthand shapes is exercised on deeper edges
    # via the unit tests in test_render_dot.py. Here just confirm
    # all three children render as nodes inside the frame.
    assert '"top.u_named"' in output
    assert '"top.u_pos"' in output
    assert '"top.u_short"' in output
    # Param overrides on the named node show the full `.PARAM(value)`
    # form, one per ``\l``-aligned line; the positional node shows bare
    # values in the same multi-line shape.
    assert r"#(\l  .WIDTH(16)\l  .DEPTH(32)\l)" in output
    assert r"#(\l  16\l  32\l)" in output
