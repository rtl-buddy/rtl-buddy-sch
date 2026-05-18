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
    # Named form preserves both halves.
    assert '"top" -> "top.u_named" [label=".clk(clk), .rst_n(rst_n), .q(q)"];' in output
    # Positional form drops the leading `.port` — just net text.
    assert '"top" -> "top.u_pos" [label="clk, rst_n, q"];' in output
    # Shorthand form is bare `.port`.
    assert '"top" -> "top.u_short" [label=".clk, .rst_n, .q"];' in output
    # Param overrides on the named node show the full `.PARAM(value)`
    # form; the positional node should show bare values.
    assert "#(.WIDTH(16), .DEPTH(32))" in output
    assert "#(16, 32)" in output
