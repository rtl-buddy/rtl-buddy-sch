"""End-to-end coverage for interface ports on the diagram root (rtl-buddy-sch#195).

Two failures on the block diagram's boundary flags:

1. a modport-less root port (``bus_if gen``) got no flag, so the edge
   named an undeclared ``_in_gen`` node that Graphviz invented as a
   default box inside the frame;
2. an output-modport root port (``bus_if.src out_bus``) was flagged on
   the input side and its edge pointed into the child driving it.

Parses ``tests/fixtures/iface_root_ports`` with Verible, so it skips
when the binary is absent.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from rtl_buddy_view import elk_export
from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.connectivity import boundary_directions
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "iface_root_ports"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_EDGE_RE = re.compile(r'^\s*"([^"]+)" -> "([^"]+)" \[', re.M)
_FLAG_RE = re.compile(
    r'^\s*"(_(?:in|out)_[^"]+)" \[shape=plaintext, label="([^"]*)"', re.M
)


def _table():
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def _dot(top: str) -> str:
    table = _table()
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, top), out, block_diagram=True, module_table=table
    )
    return out.getvalue()


def _rank_of(text: str, node: str) -> str:
    """``source`` / ``sink`` — the rank block the flag is declared in."""
    for block in re.finditer(r"\{ rank=(source|sink);(.*?)\}", text, re.S):
        if f'"{node}"' in block.group(2):
            return block.group(1)
    raise AssertionError(f"{node} is not in a rank block")


def test_every_root_port_gets_a_declared_flag() -> None:
    text = _dot("top3")
    flags = dict(_FLAG_RE.findall(text))
    assert set(flags) == {"_in_gen", "_out_out_bus"}


def test_an_output_modport_root_port_is_an_output_flag() -> None:
    text = _dot("top3")
    assert _rank_of(text, "_out_out_bus") == "sink"
    assert ("top3.u_s", "_out_out_bus") in _EDGE_RE.findall(text)
    assert "_in_out_bus" not in text


def test_a_generic_root_port_takes_the_direction_of_the_modport_it_binds() -> None:
    text = _dot("top3")
    assert _rank_of(text, "_in_gen") == "source"
    assert dict(_FLAG_RE.findall(text))["_in_gen"] == "gen ▶"
    assert ("_in_gen", "top3.u_k") in _EDGE_RE.findall(text)


def test_a_generic_root_port_bound_to_a_source_is_an_output() -> None:
    text = _dot("top_gen")
    assert _rank_of(text, "_out_g_out") == "sink"
    assert ("top_gen.u_s", "_out_g_out") in _EDGE_RE.findall(text)


def test_an_unresolvable_root_port_is_a_bidirectional_flag() -> None:
    flags = dict(_FLAG_RE.findall(_dot("top_gen")))
    for name in ("g_both", "g_none", "g_free"):
        assert flags[f"_in_{name}"].endswith("↔"), name


def test_boundary_directions_of_the_root() -> None:
    table = _table()
    assert boundary_directions(table.modules_by_name["top3"], table) == {
        "gen": "input",
        "out_bus": "output",
    }
    assert boundary_directions(table.modules_by_name["top_gen"], table) == {
        "g_out": "output",
        "g_both": None,
        "g_none": None,
        "g_free": None,
    }


def test_elk_root_pins_carry_the_resolved_directions() -> None:
    table = _table()
    payload = elk_export.export_elk(build_hierarchy(table, "top3"), table)
    ports = {p["rb"]["name"]: p["rb"] for p in payload["ports"]}
    assert ports["gen"]["direction"] == "input"
    assert ports["gen"]["width"] is None
    assert ports["out_bus"]["direction"] == "output"
    edges = {e["id"] for e in payload["edges"]}
    assert edges == {"top3.u_s:o->top3:out_bus", "top3:gen->top3.u_k:i"}
