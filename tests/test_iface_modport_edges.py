"""End-to-end coverage for whole-interface (modport) bindings (rtl-buddy-sch#194).

Siblings bound to one interface instance through modport ports
(``bus_if.src o`` / ``bus_if.snk i``) used to get no edge: an
interface port has no declared direction, so both pins landed in the
unknown bucket and nothing was drawn. The interface instance itself
rendered as an orphan blackbox box, and the ELK export carried
``rb.direction: null`` on every modport-typed pin.

Parses ``tests/fixtures/iface_modport_edges`` with Verible, so it
skips when the binary is absent — the gate the other fixture-driven
tests use.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from rtl_buddy_view import elk_export
from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "iface_modport_edges"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_EDGE_RE = re.compile(
    r'^\s*"([^"]+)" -> "([^"]+)" \[color="([^"]+)".*?xlabel="([^"]*)"', re.M
)
_SIDE = "#94a3b8"


def _table():
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def _dot(top: str, *, block_diagram: bool = True) -> str:
    table = _table()
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, top),
        out,
        block_diagram=block_diagram,
        module_table=table,
    )
    return out.getvalue()


def _block_edges(top: str) -> list[tuple[str, str, str, bool]]:
    """``(src, dst, label, is_side)`` for every block-diagram edge."""
    return sorted(
        (src, dst, label.replace(r"\l", " ").strip(), color == _SIDE)
        for src, dst, color, label in _EDGE_RE.findall(_dot(top))
    )


def test_siblings_on_one_interface_get_modport_oriented_edges() -> None:
    edges = _block_edges("top")
    assert ("top.u_prod", "top.u_cons", "shared", False) in edges
    assert ("top.u_s0", "top.u_s1", "mid", False) in edges


def test_a_monitor_modport_is_a_side_tap() -> None:
    """``mon`` is all inputs and reads ``snk``'s ready: an observer."""
    assert ("top.u_prod", "top.u_mon", "shared", True) in _block_edges("top")


def test_no_edge_runs_against_the_modports() -> None:
    sibling = {
        (src, dst)
        for src, dst, _, _ in _block_edges("top")
        if not src.startswith("_") and not dst.startswith("_")
    }
    assert sibling == {
        ("top.u_prod", "top.u_cons"),
        ("top.u_prod", "top.u_mon"),
        ("top.u_s0", "top.u_s1"),
    }


def test_the_interface_instance_is_the_wire_not_a_box() -> None:
    text = _dot("top")
    assert '"top.shared"' not in text
    assert '"top.mid"' not in text
    assert "(blackbox)" not in text


def test_the_hierarchy_view_still_shows_interface_instances() -> None:
    """Pruning is a dataflow-drawing decision; the hierarchy keeps them."""
    text = _dot("top", block_diagram=False)
    assert '"top.shared"' in text
    assert '"top.mid"' in text


def test_a_modport_less_port_faces_the_modport_pins_it_meets() -> None:
    assert _block_edges("top_generic") == [
        ("top_generic.u_p", "top_generic.u_t1", "a", False),
        ("top_generic.u_t2", "top_generic.u_c", "b", False),
    ]


def test_a_member_select_meets_the_modports_per_signal_direction() -> None:
    """``u_r`` drives ``bus.r``, which the ``src`` modport reads."""
    assert _block_edges("top_member") == [
        ("top_member.u_r", "top_member.u_p", "bus.r", False),
    ]


def test_elk_directions_come_from_the_modport() -> None:
    table = _table()
    payload = elk_export.export_elk(build_hierarchy(table, "top"), table)
    directions = {
        port["id"]: port["rb"]["direction"]
        for node in (payload, *payload["children"])
        for port in node["ports"]
    }
    assert directions["top.u_prod:o"] == "output"
    assert directions["top.u_cons:i"] == "input"
    assert directions["top.u_mon:m"] == "input"
    assert directions["top.u_s0:i"] == "input"
    assert directions["top.u_s0:o"] == "output"
    assert directions["top.u_s1:i"] == "input"
    assert directions["top.u_s1:o"] == "output"
    assert directions["top:in_bus"] == "input"
    assert directions["top:out_bus"] == "output"


def test_elk_edges_land_on_the_modport_pins() -> None:
    table = _table()
    payload = elk_export.export_elk(build_hierarchy(table, "top"), table)
    edges = {
        e["id"]: (e["rb"]["nets"], e["rb"]["bits"], e["rb"]["emphasis"])
        for e in payload["edges"]
    }
    assert edges["top.u_prod:o->top.u_cons:i"] == (["shared"], None, None)
    assert edges["top.u_prod:o->top.u_mon:m"] == (["shared"], None, "side")
    assert edges["top.u_s0:o->top.u_s1:i"] == (["mid"], None, None)


def test_elk_keeps_the_interface_instance_nodes() -> None:
    """The ELK node set is the hierarchy (#165); only dot prunes."""
    table = _table()
    payload = elk_export.export_elk(build_hierarchy(table, "top"), table)
    ids = {child["id"] for child in payload["children"]}
    assert {"top.shared", "top.mid"} <= ids


def test_elk_reports_no_width_for_a_modport_less_interface_port() -> None:
    table = _table()
    payload = elk_export.export_elk(build_hierarchy(table, "top_generic"), table)
    ports = {
        port["id"]: port["rb"]
        for child in payload["children"]
        for port in child["ports"]
    }
    assert ports["top_generic.u_t1:b"]["width"] is None
    assert ports["top_generic.u_t1:b"]["direction"] is None
