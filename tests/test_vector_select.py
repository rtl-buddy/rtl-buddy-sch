"""End-to-end coverage for plain-vector element selects (rtl-buddy-sch#208).

A pin bound to an element of a plain vector (``.q(dv[SR_A])``) is its
own net, keyed ``dv[SR_A]`` — the vector counterpart of the interface
member keys of rtl-buddy-sch#193. Before the fix every select of ``dv``
collapsed onto ``dv`` (all-to-all edges between its producers and
consumers), and the index constant ``SR_A`` became a net of its own,
joining the reset select ``s_rst_l[SR_A]`` to the data select
``dv[SR_A]`` so the reset was no longer filtered as one.

Parses ``tests/fixtures/vector_select`` with Verible, so it skips when
the binary is absent — the gate the other fixture-driven tests use.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.connectivity import scope_connectivity
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "vector_select"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_EDGE_RE = re.compile(r'^\s*"([^"]+)" -> "([^"]+)" \[.*?xlabel="([^"]*)"', re.M)


def _table():
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def _block_edges(top: str) -> list[tuple[str, str, str]]:
    table = _table()
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, top), out, block_diagram=True, module_table=table
    )
    return sorted(
        (src, dst, label.replace(r"\l", " ").strip())
        for src, dst, label in _EDGE_RE.findall(out.getvalue())
    )


def test_issue_208_repro_draws_one_edge_per_element() -> None:
    assert _block_edges("top6") == [
        ("top6.u_s0", "top6.u_k0", "dv[SR_A]"),
        ("top6.u_s1", "top6.u_k1", "dv[SR_B]"),
    ]


def test_index_constants_are_not_nets_and_reset_selects_stay_resets() -> None:
    table = _table()
    edges = scope_connectivity(table.modules_by_name["top6"], table)
    nets = {net for edge in edges for net in edge.nets}
    assert nets == {"dv[SR_A]", "dv[SR_B]"}
    # No edge carries the reset select or reaches a rst pin.
    assert all("rst" not in edge.dst_pins for edge in edges)
    assert all(not net.startswith("s_rst_l") for net in nets)


def test_whole_vector_references_meet_element_selects() -> None:
    assert _block_edges("top8") == [
        # Element drivers reach the whole reader and the non-constant
        # select (which falls back to the vector), but not each other.
        ("top8.u_e1", "top8.u_v", "dv[1]"),
        ("top8.u_e1", "top8.u_wd", "dv[1]"),
        # A whole driver reaches an element reader, a whole reader
        # (labelled once, not once per element) and an output port
        # through an assign.
        ("top8.u_w", "_out_o", "o"),
        ("top8.u_w", "top8.u_e0", "bus[LO]"),
        ("top8.u_w", "top8.u_wb", "bus"),
        # An element written by an assign is still that element.
        ("top8.u_x", "top8.u_v", "dv[0]"),
        ("top8.u_x", "top8.u_wd", "dv[0]"),
    ]


def test_a_variable_index_is_read_by_nobody() -> None:
    """``dv[sel]``'s ``sel`` is an index, not dataflow into ``u_v``."""
    table = _table()
    edges = scope_connectivity(table.modules_by_name["top8"], table)
    assert not [e for e in edges if e.src == ("port", "sel")]


def test_whole_to_whole_keeps_the_vector_width() -> None:
    table = _table()
    edges = scope_connectivity(table.modules_by_name["top8"], table)
    (edge,) = [e for e in edges if e.dst == ("inst", "u_wb")]
    assert edge.nets == ("bus",)
    assert edge.bits == 2
