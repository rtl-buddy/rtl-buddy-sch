"""Procedural data flow and the ``--prune-flags`` guard (rtl-buddy-sch#210).

The block diagram built flow edges from continuous assignments only, so
a root input the top registers before handing on (``flag_a_q <=
flag_a;`` in an ``always_ff``) got no edge, and ``--prune-flags`` then
dropped it like an unconnected clock. Assignments in ``always*`` blocks
now add flow from their right-hand side into their left; the ``if`` /
``case`` conditions that steer them do not, so the reset in ``if
(!rst_n)`` stays out of the data groups.

The fixture-driven tests parse ``tests/fixtures/procedural_flow`` with
Verible and skip when the binary is absent; the synthetic ones do not.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.connectivity import logic_nets, scope_connectivity
from rtl_buddy_view.extractor import (
    Assign,
    Instance,
    Module,
    ModuleTable,
    NetDecl,
    Port,
    PortConnection,
)
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.hints import NetHints
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "procedural_flow"

needs_verible = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_EDGE_RE = re.compile(r'^\s*"([^"]+)" -> "([^"]+)" \[.*?xlabel="([^"]*)"', re.M)
_FLAG_RE = re.compile(r'^\s*"(_(?:in|out)_[^"]+)" \[', re.M)


def _table() -> ModuleTable:
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def _render(*, prune_flags: bool = False) -> str:
    table = _table()
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, "pf_top"),
        out,
        block_diagram=True,
        module_table=table,
        prune_flags=prune_flags,
    )
    return out.getvalue()


def _edges(text: str) -> list[tuple[str, str, str]]:
    return sorted(
        (src, dst, label.replace(r"\l", " ").strip())
        for src, dst, label in _EDGE_RE.findall(text)
    )


# --- frontend ---------------------------------------------------------------


@needs_verible
def test_frontend_extracts_procedural_assigns_and_conditions() -> None:
    top = _table().modules_by_name["pf_top"]
    assert [(a.lhs_text, a.rhs_text) for a in top.procedural_assigns] == [
        ("flag_a_q", "1'b0"),
        ("flag_a_q", "flag_a"),
        ("held", "flag_a_q"),
    ]
    assert top.procedural_conditions == ("(!rst_a_n)", "(en_a)")
    # Continuous assigns are unchanged: the top has none.
    assert top.assigns == ()


@needs_verible
def test_frontend_reads_every_procedural_assignment_shape(tmp_path: Path) -> None:
    src = tmp_path / "m.sv"
    src.write_text(
        "module m (input logic clk, input logic [3:0] a, input logic en,\n"
        "          input logic [1:0] s, output logic [3:0] y);\n"
        "  logic [3:0] c;\n"
        "  initial c = 4'd0;\n"
        "  function automatic logic f(input logic x); f = x; endfunction\n"
        "  always_comb begin\n"
        "    for (int i = 0; i < 4; i++) y[i] = a[i] & en;\n"
        "    while (en) y = a;\n"
        "    unique case (s) 2'd0: y = c; default: y += a; endcase\n"
        "  end\n"
        "  always_latch if (en) c <= #1 a;\n"
        "endmodule\n"
    )
    m = parse_to_modules([src], frontend=Frontend.verible).modules_by_name["m"]
    assert [(a.lhs_text, a.rhs_text) for a in m.procedural_assigns] == [
        ("i", "0"),
        ("y[i]", "a[i] & en"),
        ("y", "a"),
        ("y", "c"),
        ("y", "a"),
        ("c", "a"),
    ]
    assert m.procedural_conditions == (
        "(int i = 0; i < 4; i++)",
        "en",
        "(s)",
        "2'd0",
        "(en)",
    )


# --- block diagram ----------------------------------------------------------


@needs_verible
def test_registered_root_input_reaches_the_block_it_feeds() -> None:
    assert _edges(_render()) == [
        ("_in_arst_a_n", "pf_top.u_reset_sync_a", "arst_a_n"),
        ("_in_arst_b_n", "pf_top.u_reset_sync_b", "arst_b_n"),
        ("_in_flag_a", "pf_top.u_flag_sync", "flag_a_q"),
        ("_in_flag_a", "pf_top.u_held", "held"),
        ("pf_top.u_flag_sync", "_out_flag_b", "flag_b"),
    ]


@needs_verible
def test_a_reset_steering_an_always_ff_is_not_a_data_edge() -> None:
    """``if (!rst_a_n)`` is a condition, not flow: ``rst_a_n`` keeps a
    reset-only group, so its synchronizer draws no data edge."""
    table = _table()
    edges = scope_connectivity(table.modules_by_name["pf_top"], table)
    assert not [e for e in edges if e.src[1].startswith("u_reset_sync")]
    assert not [e for e in edges if "rst_n" in e.dst_pins]
    assert all(not n.startswith("rst_") for e in edges for n in e.nets)


@needs_verible
def test_prune_flags_keeps_ports_the_body_logic_reads() -> None:
    """``flag_a`` (now edged) and ``en_a`` (read only by an ``if``) stay;
    the clocks, read only by event controls and pins, still go."""
    assert sorted(_FLAG_RE.findall(_render(prune_flags=True))) == [
        "_in_arst_a_n",
        "_in_arst_b_n",
        "_in_en_a",
        "_in_flag_a",
        "_out_flag_b",
    ]


# --- synthetic --------------------------------------------------------------


def _port(name: str, direction: str, type_text: str = "logic") -> Port:
    return Port(name=name, direction=direction, type_text=type_text, location=None)  # type: ignore[arg-type]


def _inst(name: str, module_name: str, *conns: tuple[str, str]) -> Instance:
    return Instance(
        name=name,
        module_name=module_name,
        param_overrides=(),
        port_connections=tuple(
            PortConnection(port_name=p, net_expr_text=n, location=None)
            for p, n in conns
        ),
        location=None,
    )


def _assign(lhs: str, rhs: str) -> Assign:
    return Assign(lhs_text=lhs, rhs_text=rhs, location=None)


_SRC = Module(
    name="src",
    ports=(_port("q", "output"),),
    parameters=(),
    instances=(),
    location=None,
)
_SINK = Module(
    name="sink",
    ports=(_port("d", "input"),),
    parameters=(),
    instances=(),
    location=None,
)


def test_procedural_assign_chains_like_a_continuous_one() -> None:
    top = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(
            _inst("u_src", "src", ("q", "a")),
            _inst("u_sink", "sink", ("d", "c")),
        ),
        location=None,
        assigns=(_assign("b", "a"),),
        procedural_assigns=(_assign("c", "b"),),
        procedural_conditions=("(go)",),
    )
    table = ModuleTable(modules_by_name={m.name: m for m in (top, _SRC, _SINK)})
    (edge,) = scope_connectivity(top, table)
    assert (edge.src, edge.dst, edge.nets) == (
        ("inst", "u_src"),
        ("inst", "u_sink"),
        ("c",),
    )


def test_a_loop_variable_index_is_not_constant() -> None:
    """``y[i]`` under ``for (int i …)`` writes no one element: it falls
    back to ``y``, which meets the ``y[0]`` reader."""
    top = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(
            _inst("u_src", "src", ("q", "a")),
            _inst("u_sink", "sink", ("d", "y[0]")),
        ),
        location=None,
        net_decls=(NetDecl(name="y", type_text="logic [3:0]", location=None),),
        procedural_assigns=(_assign("i", "0"), _assign("y[i]", "a")),
    )
    table = ModuleTable(modules_by_name={m.name: m for m in (top, _SRC, _SINK)})
    (edge,) = scope_connectivity(top, table)
    assert (edge.src, edge.dst, edge.nets) == (
        ("inst", "u_src"),
        ("inst", "u_sink"),
        ("y[0]",),
    )


def test_logic_nets_reads_assignments_and_conditions_but_not_clocks() -> None:
    top = Module(
        name="top",
        ports=(),
        parameters=(),
        instances=(),
        location=None,
        assigns=(_assign("o", "x & mem[addr]"),),
        procedural_assigns=(_assign("q", "d"),),
        procedural_conditions=("(!rst_n)", "(en)", "(sysclk)"),
    )
    assert logic_nets(top) == frozenset({"o", "x", "mem", "addr", "q", "d", "en"})
    hinted = logic_nets(
        top,
        {
            "sysclk": NetHints(classification="data"),
            "en": NetHints(classification="reset"),
        },
    )
    assert "sysclk" in hinted and "en" not in hinted
