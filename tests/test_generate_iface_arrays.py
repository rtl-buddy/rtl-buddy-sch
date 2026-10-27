"""Interfaces declared inside a generate block (rtl-buddy-sch#218).

``bus_if sub [0:1] ();`` inside ``for … begin : g`` is a fresh array
per iteration — ``g[0].sub``, ``g[1].sub`` — exactly like a ``logic``
net declared there. The unroller used to qualify only the nets, so a
sibling's ``.src(sub[1])`` kept the bare module-scope spelling ``sub``:
an array binding and an element binding then never met (no edge), and
a scalar interface bound in every iteration met *every* iteration
(cross-iteration edges). These pin the qualified bindings, the
per-iteration edges against their module-scope and ``logic`` controls,
the symbolic / conditional / nested forms, and every edge-carrying
export.

``tests/fixtures/generate_iface_arrays/repro8.sv`` holds the issue's
repro verbatim (``top8``) and its module-scope control (``top9``);
``gen_iface.sv`` the rest. Parses with Verible, so the fixture tests
skip when the binary is absent.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import jsonschema
import pytest

from rtl_buddy_view import elk_export
from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.connectivity import NetEdge, net_keys, scope_connectivity
from rtl_buddy_view.extractor import (
    GenerateLoop,
    Instance,
    ModuleTable,
    PortConnection,
)
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.generate import elaborate
from rtl_buddy_view.graph import build_hierarchy, is_interface_instance
from rtl_buddy_view.query import walk
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render, mermaid

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "generate_iface_arrays"
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "view-v1.json"

needs_verible = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)

_EDGE_RE = re.compile(r'^\s*"([^"]+)" -> "([^"]+)" \[.*?xlabel="([^"]*)"', re.M)


@pytest.fixture(scope="module")
def table() -> ModuleTable:
    if find_binary("verible-verilog-syntax") is None:
        pytest.skip("verible-verilog-syntax not found")
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def _block_edges(table: ModuleTable, top: str) -> list[tuple[str, str, str]]:
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, top), out, block_diagram=True, module_table=table
    )
    return sorted(_EDGE_RE.findall(out.getvalue()))


def _inst_edges(table: ModuleTable, top: str) -> list[tuple[str, str, tuple]]:
    return [
        (e.src[1], e.dst[1], e.nets)
        for e in scope_connectivity(table.modules_by_name[top], table)
        if e.src[0] == "inst" and e.dst[0] == "inst"
    ]


def _bindings(table: ModuleTable, top: str, inst: str) -> dict[str, str]:
    module = table.modules_by_name[top]
    found = next(i for i in module.instances if i.name == inst)
    return {c.port_name or "": c.net_expr_text for c in found.port_connections}


# --- unit level: no Verible ---------------------------------------------------


def _inst(name: str, module: str, **conns: str) -> Instance:
    return Instance(
        name=name,
        module_name=module,
        param_overrides=(),
        port_connections=tuple(
            PortConnection(port_name=p, net_expr_text=t, location=None)
            for p, t in conns.items()
        ),
        location=None,
    )


def test_unroll_qualifies_block_local_instances() -> None:
    loop = GenerateLoop(
        label="g",
        genvar="t",
        init_text="genvar t = 0",
        cond_text="t < 2",
        step_text="t++",
        items=(
            _inst("sub", "bus_if"),
            _inst("i_mrg", "mrg_m", sub="sub", q="q[t]"),
            _inst("i_lpb", "lpb_m", src="sub[1]"),
            _inst("i_far", "lpb_m", src="top_if.sub"),
        ),
    )
    instances, _ = elaborate((loop,), {})
    bound = {
        i.name: {c.port_name: c.net_expr_text for c in i.port_connections}
        for i in instances
    }
    assert bound["g[1].i_mrg"] == {"sub": "g[1].sub", "q": "q[1]"}
    assert bound["g[0].i_lpb"] == {"src": "g[0].sub[1]"}
    # A member that merely shares the name is not the local instance.
    assert bound["g[0].i_far"] == {"src": "top_if.sub"}


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("g[0].sub", ("g[0].sub",)),
        ("g[0].sub[1]", ("g[0].sub",)),
        ("g[0].sub[1].v", ("g[0].sub[1].v",)),
        ("g[1].sub[0].v & g[0].sub[0].v", ("g[1].sub[0].v", "g[0].sub[0].v")),
        # Not the scoped interface: a longer name, or a plain local net.
        ("g[0].subx", ("g[0].subx",)),
        ("g[0].w", ("g[0].w",)),
    ],
)
def test_generate_scoped_interface_keys(text: str, keys: tuple[str, ...]) -> None:
    assert net_keys(text, {"g", "g[0].sub", "g[1].sub"}) == keys


# --- the issue's repro ----------------------------------------------------------


@needs_verible
def test_repro_bindings_name_the_iterations_array(table: ModuleTable) -> None:
    assert _bindings(table, "top8", "g[0].i_mrg") == {"sub": "g[0].sub", "q": "q[0]"}
    assert _bindings(table, "top8", "g[1].i_lpb") == {"src": "g[1].sub[1]"}


@needs_verible
def test_repro_block_diagram_draws_each_iterations_edge(table: ModuleTable) -> None:
    assert _block_edges(table, "top8") == [
        ("top8.g[0].i_lpb", "top8.g[0].i_mrg", r"g[0].sub\l"),
        ("top8.g[0].i_mrg", "_out_q", r"q[0]\l"),
        ("top8.g[1].i_lpb", "top8.g[1].i_mrg", r"g[1].sub\l"),
        ("top8.g[1].i_mrg", "_out_q", r"q[1]\l"),
    ]


@needs_verible
def test_module_scope_control(table: ModuleTable) -> None:
    assert _block_edges(table, "top9") == [
        ("top9.i_lpb", "top9.i_mrg", r"sub\l"),
        ("top9.i_mrg", "_out_q", r"q\l"),
    ]


@needs_verible
def test_logic_in_generate_control(table: ModuleTable) -> None:
    assert _inst_edges(table, "gen_logic") == [
        ("g[0].i_drv", "g[0].i_rd", ("g[0].w",)),
        ("g[1].i_drv", "g[1].i_rd", ("g[1].w",)),
    ]


@needs_verible
def test_scalar_interface_iterations_do_not_cross_connect(
    table: ModuleTable,
) -> None:
    # Before #218 every ``.src(sub)`` met every ``.sub(sub)``: four
    # edges, two of them g[0] -> g[1] and back.
    assert _inst_edges(table, "gen_scalar") == [
        ("g[0].i_lpb", "g[0].i_mrg", ("g[0].sub",)),
        ("g[1].i_lpb", "g[1].i_mrg", ("g[1].sub",)),
    ]


def _as_iteration(edge: NetEdge, prefix: str) -> tuple[str, str, tuple]:
    return (
        prefix + edge.src[1],
        prefix + edge.dst[1],
        tuple(prefix + n for n in edge.nets),
    )


@needs_verible
def test_each_iteration_draws_what_module_scope_draws(table: ModuleTable) -> None:
    """Element bindings and a member select key exactly as at module scope."""
    control = [
        e
        for e in scope_connectivity(table.modules_by_name["mod_members"], table)
        if e.src[0] == "inst" and e.dst[0] == "inst"
    ]
    # The member select keeps its own net: only i_l1 (sub[1]) reaches i_b.
    assert ("i_l1", "i_b", ("sub[1].v",)) in [
        (e.src[1], e.dst[1], e.nets) for e in control
    ]
    assert not any(e.src[1] == "i_l0" and e.dst[1] == "i_b" for e in control)
    expected = sorted(_as_iteration(e, f"g[{t}].") for t in range(2) for e in control)
    assert sorted(_inst_edges(table, "gen_members")) == expected


@needs_verible
def test_symbolic_iteration_draws_one_edge(table: ModuleTable) -> None:
    assert _block_edges(table, "gen_sym") == [
        ("gen_sym.g[*].i_lpb", "gen_sym.g[*].i_mrg", r"g[*].sub\l"),
        ("gen_sym.g[*].i_mrg", "_out_q", r"q\l"),
    ]


@needs_verible
def test_conditional_arm_and_nested_loop(table: ModuleTable) -> None:
    assert _inst_edges(table, "gen_cond") == [
        ("g.i_lpb", "g.i_mrg", ("g.sub",)),
    ]
    assert _bindings(table, "gen_nest", "o[1].i[0].i_lpb") == {"src": "o[1].sub[0]"}
    assert _inst_edges(table, "gen_nest") == [
        ("o[0].i[0].i_lpb", "o[0].i_mrg", ("o[0].sub",)),
        ("o[0].i[1].i_lpb", "o[0].i_mrg", ("o[0].sub",)),
        ("o[1].i[0].i_lpb", "o[1].i_mrg", ("o[1].sub",)),
        ("o[1].i[1].i_lpb", "o[1].i_mrg", ("o[1].sub",)),
    ]


# --- the interface instance is the wire, in every export ------------------------


@needs_verible
def test_generate_local_interface_is_not_a_box(table: ModuleTable) -> None:
    root = build_hierarchy(table, "top8")
    ifaces = sorted(
        n.instance_path for n in walk(root) if is_interface_instance(n, table)
    )
    assert ifaces == ["top8.g[0].sub", "top8.g[1].sub"]
    out = io.StringIO()
    dot_render.render(root, out, block_diagram=True, module_table=table)
    for path in ifaces:
        assert f'"{path}"' not in out.getvalue()


@needs_verible
def test_elk_and_view_json_carry_the_edges(table: ModuleTable) -> None:
    root = build_hierarchy(table, "top8")
    want = [
        ("top8.g[0].i_lpb:src", "top8.g[0].i_mrg:sub", ["g[0].sub"]),
        ("top8.g[0].i_mrg:q", "top8:q", ["q[0]"]),
        ("top8.g[1].i_lpb:src", "top8.g[1].i_mrg:sub", ["g[1].sub"]),
        ("top8.g[1].i_mrg:q", "top8:q", ["q[1]"]),
    ]

    def ends(payload: dict) -> list[tuple[str, str, list[str]]]:
        return sorted(
            (e["sources"][0], e["targets"][0], e["rb"]["nets"])
            for e in payload["edges"]
        )

    elk = elk_export.export_elk(root, table)
    assert ends(elk) == want
    children = {c["id"]: c["rb"] for c in elk["children"]}
    assert children["top8.g[0].sub"]["is_interface"] is True

    out = io.StringIO()
    json_render.render(root, out, module_table=table)
    view = json.loads(out.getvalue())
    jsonschema.validate(instance=view, schema=json.loads(SCHEMA_PATH.read_text()))
    assert ends(view["layout"]["elk"]) == want
    nodes = {n["id"]: n for n in view["nodes"]}
    ports = {p["name"]: p["expr"] for p in nodes["top8.g[1].i_lpb"]["ports"]}
    # An interface pin is listed per modport signal, as at module scope.
    assert ports == {"src.v": "g[1].sub[1]", "src.d": "g[1].sub[1]"}


@needs_verible
def test_mermaid_labels_name_the_iterations_array(table: ModuleTable) -> None:
    out = io.StringIO()
    mermaid.render(build_hierarchy(table, "top8"), out)
    assert '-->|".src(g[1].sub[1])"| top8_g_1__i_lpb' in out.getvalue()
