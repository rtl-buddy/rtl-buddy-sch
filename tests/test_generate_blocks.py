"""Instances inside generate blocks (rtl-buddy-sch#209).

A ``for (genvar …) begin : g`` loop, an ``if`` / ``case`` generate
block and an unnamed generate block all used to contribute no child
nodes, so their instances were missing from every output and their
connections were lost. These pin the unrolled hierarchy (named the way
slang and simulators name it), the per-iteration bindings, the
symbolic and conditional fallbacks, and every renderer over the issue's
repro.

``tests/fixtures/generate_blocks/repro7.sv`` is the issue's repro
verbatim; ``gen_if.sv`` covers conditional generates and
``gen_nested.sv`` a for-in-for array, an unresolvable bound and
per-iteration local nets. Parses with Verible, so the fixture tests
skip when the binary is absent — the gate the other fixture-driven
tests use. The evaluator tests need nothing.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import jsonschema
import pytest

from rtl_buddy_view import elk_export, graph_export
from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.connectivity import scope_connectivity
from rtl_buddy_view.extractor import GenerateLoop, ModuleTable
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.generate import (
    MAX_ITERATIONS,
    eval_bool,
    eval_const,
    eval_value,
    loop_indices,
)
from rtl_buddy_view.graph import HierNode, build_hierarchy
from rtl_buddy_view.query import walk
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render, mermaid, tree

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "generate_blocks"
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


def _paths(node: HierNode) -> list[str]:
    return [n.instance_path for n in walk(node)]


def _child(node: HierNode, name: str) -> HierNode:
    return next(c for c in node.children if c.instance and c.instance.name == name)


def _bindings(node: HierNode) -> dict[str, str]:
    assert node.instance is not None
    return {c.port_name or "": c.net_expr_text for c in node.instance.port_connections}


# --- the issue's repro: graph level -------------------------------------------


@needs_verible
def test_repro_loop_instances_are_hierarchy_nodes(table: ModuleTable) -> None:
    root = build_hierarchy(table, "top7")
    assert _paths(root) == [
        "top7",
        "top7.bq",
        "top7.g[0].u_s",
        "top7.g[0].u_k",
        "top7.g[1].u_s",
        "top7.g[1].u_k",
    ]
    names = [c.instance.name for c in root.children if c.instance is not None]
    assert names == ["bq", "g[0].u_s", "g[0].u_k", "g[1].u_s", "g[1].u_k"]
    assert not any(c.is_conditional for c in root.children)


@needs_verible
def test_repro_bindings_carry_the_iterations_genvar_value(
    table: ModuleTable,
) -> None:
    root = build_hierarchy(table, "top7")
    assert _bindings(_child(root, "g[0].u_s")) == {"clk": "s_clk", "q": "bq[0].v"}
    assert _bindings(_child(root, "g[1].u_k")) == {"clk": "s_clk", "d": "bq[1].v"}


@needs_verible
def test_repro_connectivity_pairs_each_iteration(table: ModuleTable) -> None:
    edges = scope_connectivity(table.modules_by_name["top7"], table)
    assert [(e.src, e.dst, e.nets) for e in edges] == [
        (("inst", "g[0].u_s"), ("inst", "g[0].u_k"), ("bq[0].v",)),
        (("inst", "g[1].u_s"), ("inst", "g[1].u_k"), ("bq[1].v",)),
    ]


# --- the issue's repro: every renderer ----------------------------------------


@needs_verible
def test_repro_tree(table: ModuleTable) -> None:
    out = io.StringIO()
    tree.render(build_hierarchy(table, "top7"), out, links=False)
    # The issue's expected tree. ``bq`` keeps its ``(blackbox)`` tag: an
    # interface instance has no module definition, which elk.json
    # documents as ``is_blackbox: true`` + ``is_interface: true``
    # (#205) — a separate decision from the missing generate rows.
    assert out.getvalue() == (
        "top7\n"
        "├── bq : b_if (blackbox)\n"
        "├── g[0].u_s : src_m\n"
        "├── g[0].u_k : sink_m\n"
        "├── g[1].u_s : src_m\n"
        "└── g[1].u_k : sink_m\n"
    )


@needs_verible
def test_repro_block_diagram(table: ModuleTable) -> None:
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, "top7"),
        out,
        block_diagram=True,
        module_table=table,
    )
    edges = sorted(_EDGE_RE.findall(out.getvalue()))
    assert edges == [
        ("top7.g[0].u_s", "top7.g[0].u_k", r"bq[0].v\l"),
        ("top7.g[1].u_s", "top7.g[1].u_k", r"bq[1].v\l"),
    ]


@needs_verible
def test_repro_dot_tree_declares_every_iteration(table: ModuleTable) -> None:
    out = io.StringIO()
    dot_render.render(build_hierarchy(table, "top7"), out, module_table=table)
    text = out.getvalue()
    for path in ("top7.g[0].u_s", "top7.g[0].u_k", "top7.g[1].u_s", "top7.g[1].u_k"):
        assert f'"{path}" [label=' in text


@needs_verible
def test_repro_view_json(table: ModuleTable) -> None:
    out = io.StringIO()
    json_render.render(build_hierarchy(table, "top7"), out, module_table=table)
    payload = json.loads(out.getvalue())
    jsonschema.validate(
        instance=payload, schema=json.loads(SCHEMA_PATH.read_text("utf-8"))
    )
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert nodes["top7.g[1].u_s"]["instance_name"] == "g[1].u_s"
    ports = {p["name"]: p["expr"] for p in nodes["top7.g[1].u_s"]["ports"]}
    assert ports["q"] == "bq[1].v"
    # Unconditional nodes stay byte-compatible: no new key.
    assert all("generate_condition" not in n for n in payload["nodes"])
    assert {(e["from"], e["to"]) for e in payload["edges"]} >= {
        ("top7", "top7.g[0].u_s"),
        ("top7", "top7.g[1].u_k"),
    }


@needs_verible
def test_repro_elk(table: ModuleTable) -> None:
    payload = elk_export.export_elk(build_hierarchy(table, "top7"), table)
    children = {c["id"]: c for c in payload["children"]}
    assert set(children) == {
        "top7.bq",
        "top7.g[0].u_s",
        "top7.g[0].u_k",
        "top7.g[1].u_s",
        "top7.g[1].u_k",
    }
    assert children["top7.g[0].u_s"]["rb"]["instance_name"] == "g[0].u_s"
    assert all(c["rb"]["generate_condition"] is None for c in children.values())
    ends = sorted(
        (e["sources"][0].split(":")[0], e["targets"][0].split(":")[0])
        for e in payload["edges"]
    )
    assert ends == [
        ("top7.g[0].u_s", "top7.g[0].u_k"),
        ("top7.g[1].u_s", "top7.g[1].u_k"),
    ]


@needs_verible
def test_repro_mermaid_ids_survive_brackets(table: ModuleTable) -> None:
    out = io.StringIO()
    mermaid.render(build_hierarchy(table, "top7"), out)
    text = out.getvalue()
    # ``[`` opens a mermaid node shape, so it can never be in an id.
    assert '  top7_g_0__u_s["g[0].u_s<br/>src_m"]' in text
    assert "top7_g[" not in text


@needs_verible
def test_repro_graph_export_has_scoped_instances(table: ModuleTable) -> None:
    payload = graph_export.build_graph(build_hierarchy(table, "top7"), table)
    ids = {n["id"] for n in payload["nodes"]}
    assert {"inst:top7/top7.g[0].u_s", "inst:top7/top7.g[1].u_k"} <= ids


# --- conditional generates ----------------------------------------------------


@needs_verible
def test_conditional_arms_follow_the_parameters(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_if")
    assert [c.instance.name for c in root.children if c.instance] == [
        "g_fast.u_x",
        # The else-if chain is one construct, the second in the scope:
        # an unnamed arm is ``genblk2`` (IEEE 1800 § 27.6).
        "genblk2.u_m2",
        "c2.u_c",
        "g_bits.u_b",
        "g_nobits.u_nb",
    ]


@needs_verible
def test_undecidable_condition_keeps_both_arms_marked(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_if")
    conds = {
        c.instance.name: c.instance.generate_condition
        for c in root.children
        if c.instance is not None
    }
    assert conds["g_bits.u_b"] == "$bits(din) == 8"
    assert conds["g_nobits.u_nb"] == "!($bits(din) == 8)"
    assert conds["g_fast.u_x"] is None
    assert [c.is_conditional for c in root.children] == [
        False,
        False,
        False,
        True,
        True,
    ]


@needs_verible
def test_overrides_reelaborate_each_placement(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_if_top")
    slow = [c.instance.name for c in _child(root, "u_slow").children if c.instance]
    dflt = [c.instance.name for c in _child(root, "u_def").children if c.instance]
    assert slow[:3] == ["g_slow.u_y", "genblk2.u_m1", "c1.u_c"]
    assert dflt[:3] == ["g_fast.u_x", "genblk2.u_m2", "c2.u_c"]


@needs_verible
def test_conditional_marker_in_tree_and_outputs(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_if")
    out = io.StringIO()
    tree.render(root, out, links=False)
    assert "├── g_bits.u_b : gi_leaf (if $bits(din) == 8)\n" in out.getvalue()
    assert "├── g_fast.u_x : gi_leaf\n" in out.getvalue()

    view = io.StringIO()
    json_render.render(root, view, module_table=table)
    nodes = {n["id"]: n for n in json.loads(view.getvalue())["nodes"]}
    assert nodes["gen_if.g_nobits.u_nb"]["generate_condition"] == "!($bits(din) == 8)"
    assert "generate_condition" not in nodes["gen_if.g_fast.u_x"]

    elk = elk_export.export_elk(root, table)
    rb = {c["id"]: c["rb"] for c in elk["children"]}
    assert rb["gen_if.g_bits.u_b"]["generate_condition"] == "$bits(din) == 8"

    dot = io.StringIO()
    dot_render.render(root, dot, module_table=table)
    assert r"(if $bits(din) == 8)\l" in dot.getvalue()


# --- nested loops, symbolic bounds, local nets ---------------------------------


@needs_verible
def test_nested_for_in_for_unrolls_row_major(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_nested")
    pes = [p for p in _paths(root) if p.endswith(".u_pe")]
    assert pes == [
        f"gen_nested.row[{r}].col[{c}].u_pe" for r in range(2) for c in range(3)
    ]


@needs_verible
def test_unresolvable_bound_is_one_symbolic_iteration(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_nested")
    assert [p for p in _paths(root) if ".gsym" in p] == ["gen_nested.gsym[*].u_s"]


@needs_verible
def test_local_nets_are_per_iteration(table: ModuleTable) -> None:
    root = build_hierarchy(table, "gen_nested")
    assert _bindings(_child(root, "lane[1].u_b"))["d"] == "lane[1].w"
    edges = scope_connectivity(table.modules_by_name["gen_nested"], table)
    lane = [
        (e.src[1], e.dst[1], e.nets)
        for e in edges
        if e.src[0] == "inst" and e.src[1].startswith("lane")
    ]
    assert lane == [
        ("lane[0].u_a", "lane[0].u_b", ("lane[0].w",)),
        ("lane[1].u_a", "lane[1].u_b", ("lane[1].w",)),
    ]


@needs_verible
def test_nested_block_diagram_renders(table: ModuleTable) -> None:
    out = io.StringIO()
    dot_render.render(
        build_hierarchy(table, "gen_nested"),
        out,
        block_diagram=True,
        module_table=table,
    )
    edges = _EDGE_RE.findall(out.getvalue())
    assert ("gen_nested.lane[0].u_a", "gen_nested.lane[0].u_b", r"lane[0].w\l") in edges
    assert ("_in_din", "gen_nested.row[1].col[2].u_pe", r"din\l") in edges


# --- unnamed blocks ------------------------------------------------------------


UNNAMED_SV = """\
module un_leaf (input logic a); endmodule
module un_top #(parameter N = 2) (input logic a);
  logic genblk2;
  for (genvar i = 0; i < N; i++) begin
    un_leaf u_w (.a(a));
  end
  if (N == 2) un_leaf u_p (.a(a));
  if (1) begin : named
    if (1) un_leaf u_deep (.a(a));
  end
endmodule
"""


@needs_verible
def test_unnamed_blocks_take_lrm_default_names(tmp_path: Path) -> None:
    src = tmp_path / "un.sv"
    src.write_text(UNNAMED_SV, encoding="utf-8")
    table = parse_to_modules([src], frontend=Frontend.verible)
    root = build_hierarchy(table, "un_top")
    # Construct 1 is the loop (genblk1); construct 2 collides with the
    # declared ``genblk2`` and is zero-padded; numbering restarts in
    # each nested scope. Matches slang (pyslang 10).
    assert _paths(root) == [
        "un_top",
        "un_top.genblk1[0].u_w",
        "un_top.genblk1[1].u_w",
        "un_top.genblk02.u_p",
        "un_top.named.genblk1.u_deep",
    ]


UNTYPED_PARAM_SV = """\
module up_m #(parameter AW = 8, DW = 2 * AW, parameter int T = 3) ();
endmodule
"""


@needs_verible
def test_untyped_parameters_keep_their_names(tmp_path: Path) -> None:
    """The bound evaluator needs ``N`` in ``parameter N = 2`` by name."""
    src = tmp_path / "up.sv"
    src.write_text(UNTYPED_PARAM_SV, encoding="utf-8")
    module = parse_to_modules([src], frontend=Frontend.verible).modules_by_name["up_m"]
    assert [(p.name, p.default_text) for p in module.parameters] == [
        ("AW", "8"),
        ("DW", "2 * AW"),
        ("T", "3"),
    ]


# --- the evaluator -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "env", "expected"),
    [
        ("N", {"N": "2"}, 2),
        ("M", {"M": "N + 1", "N": "2"}, 3),
        ("$clog2(DEPTH)", {"DEPTH": "16"}, 4),
        ("$clog2(1)", {}, 0),
        ("W / 2 - 1", {"W": "9"}, 3),
        ("-7 / 2", {}, -3),
        ("7 % 3", {}, 1),
        ("1 << 4", {}, 16),
        ("2 ** 3", {}, 8),
        ("8'hFF", {}, 255),
        ("4'b1_0_1_0", {}, 10),
        ("A ? 3 : 4", {"A": "0"}, 4),
        ("(1 + 2) * 3", {}, 9),
        ("N > 1 && N < 4", {"N": "2"}, 1),
        ('MODE == "FAST"', {"MODE": '"FAST"'}, 1),
        ("!(USE)", {"USE": "1"}, 0),
    ],
)
def test_eval_const_folds(text: str, env: dict[str, str], expected: int) -> None:
    assert eval_const(text, env) == expected


@pytest.mark.parametrize(
    "text",
    [
        "UNKNOWN",
        "$bits(x)",
        "4'bx01",
        "1 / 0",
        "A",  # cyclic: A = B, B = A
        "(" * 100 + "1" + ")" * 100,
        "N +",
        "MODE == 1",  # string vs int: no guess
        "1 << 999",
    ],
)
def test_eval_const_abstains(text: str) -> None:
    env = {"A": "B", "B": "A", "MODE": '"FAST"', "N": "1"}
    assert eval_const(text, env) is None


def test_eval_value_and_bool() -> None:
    assert eval_value('"x"', {}) == "x"
    assert eval_bool("0", {}) is False
    assert eval_bool("FOO", {}) is None
    assert eval_bool("X", {"X": None}) is None


def _loop(init: str, cond: str, step: str) -> GenerateLoop:
    return GenerateLoop(
        label="g", genvar="i", init_text=init, cond_text=cond, step_text=step, items=()
    )


@pytest.mark.parametrize(
    ("init", "cond", "step", "expected"),
    [
        ("genvar i = 0", "i < N", "i++", (0, 1, 2)),
        ("i = N - 1", "i >= 0", "i -= 1", (2, 1, 0)),
        ("genvar i = 0", "i < 7", "i = i + 3", (0, 3, 6)),
        ("genvar i = 1", "i <= 8", "i = i * 2", (1, 2, 4, 8)),
        ("genvar i = 0", "i < 0", "++i", ()),
        ("genvar i = 4", "i > 0", "i >>= 1", (4, 2, 1)),
    ],
)
def test_loop_indices(
    init: str, cond: str, step: str, expected: tuple[int, ...]
) -> None:
    assert loop_indices(_loop(init, cond, step), {"N": "3"}) == expected


@pytest.mark.parametrize(
    ("init", "cond", "step"),
    [
        ("genvar i = 0", "i < W", "i++"),  # unknown bound
        ("genvar i = 0", "i < 1", "j++"),  # steps another variable
        ("genvar i = 0", "i >= 0", "i = i"),  # never moves
        ("genvar i = 0", "i >= 0", "i++"),  # runs past the cap
    ],
)
def test_loop_indices_abstain(init: str, cond: str, step: str) -> None:
    assert loop_indices(_loop(init, cond, step), {}) is None


def test_iteration_cap_is_generous_but_finite() -> None:
    assert 256 <= MAX_ITERATIONS <= 4096
    within = _loop("genvar i = 0", f"i < {MAX_ITERATIONS}", "i++")
    assert loop_indices(within, {}) == tuple(range(MAX_ITERATIONS))
