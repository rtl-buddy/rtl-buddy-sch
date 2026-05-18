"""Tests for the Phase 1 query API.

Mostly exercised against synthetic :class:`HierNode` trees so the
query semantics stand on their own without the Verible parse stack.
The :func:`source_snippet` helper hits the filesystem; those tests
use ``tmp_path`` fixtures.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view import query
from rtl_buddy_view.extractor import (
    Instance,
    Module,
    ModuleTable,
    PortConnection,
    SourceLocation,
)
from rtl_buddy_view.graph import HierNode


def _conn(port: str, net: str) -> PortConnection:
    return PortConnection(port_name=port, net_expr_text=net, location=None)


def _instance(
    name: str, module_name: str, connections: tuple[PortConnection, ...] = ()
) -> Instance:
    return Instance(
        name=name,
        module_name=module_name,
        param_overrides=(),
        port_connections=connections,
        location=None,
    )


def _node(
    path: str,
    module: str,
    *,
    instance: Instance | None = None,
    is_blackbox: bool = False,
    children: tuple[HierNode, ...] = (),
) -> HierNode:
    return HierNode(
        instance_path=path,
        module_name=module,
        instance=instance,
        module=None,
        is_blackbox=is_blackbox,
        children=children,
    )


# A small fixture tree we reuse:
#
#   top (module: top)
#   ├── u_a : sub_a
#   │   └── u_inner : leaf
#   ├── u_b : sub_a            (second instance of sub_a)
#   └── u_x : missing (blackbox)
def _tree() -> HierNode:
    leaf = _node(
        "top.u_a.u_inner",
        "leaf",
        instance=_instance("u_inner", "leaf", connections=(_conn("clk", "clk"),)),
    )
    a = _node(
        "top.u_a",
        "sub_a",
        instance=_instance("u_a", "sub_a"),
        children=(leaf,),
    )
    b = _node(
        "top.u_b",
        "sub_a",
        instance=_instance("u_b", "sub_a"),
    )
    x = _node(
        "top.u_x",
        "missing",
        instance=_instance("u_x", "missing"),
        is_blackbox=True,
    )
    return _node("top", "top", children=(a, b, x))


# --- find_module -------------------------------------------------------------


def test_find_module_hit_and_miss() -> None:
    mod = Module(name="foo", ports=(), parameters=(), instances=(), location=None)
    table = ModuleTable(modules_by_name={"foo": mod})
    assert query.find_module(table, "foo") is mod
    assert query.find_module(table, "bar") is None


# --- walk --------------------------------------------------------------------


def test_walk_visits_all_nodes_depth_first() -> None:
    paths = [n.instance_path for n in query.walk(_tree())]
    assert paths == [
        "top",
        "top.u_a",
        "top.u_a.u_inner",
        "top.u_b",
        "top.u_x",
    ]


# --- subtree -----------------------------------------------------------------


def test_subtree_finds_nested_path() -> None:
    node = query.subtree(_tree(), "top.u_a.u_inner")
    assert node is not None
    assert node.module_name == "leaf"


def test_subtree_returns_top_for_root_path() -> None:
    root = _tree()
    assert query.subtree(root, "top") is root


def test_subtree_returns_none_for_missing_path() -> None:
    assert query.subtree(_tree(), "top.u_a.does_not_exist") is None


def test_subtree_strips_leading_and_trailing_dots() -> None:
    root = _tree()
    assert query.subtree(root, ".top.u_b.") is not None


# --- instances_of ------------------------------------------------------------


def test_instances_of_finds_multiple() -> None:
    hits = query.instances_of(_tree(), "sub_a")
    assert [n.instance_path for n in hits] == ["top.u_a", "top.u_b"]


def test_instances_of_includes_blackboxes() -> None:
    hits = query.instances_of(_tree(), "missing")
    assert len(hits) == 1
    assert hits[0].is_blackbox


def test_instances_of_returns_empty_for_unknown_module() -> None:
    assert query.instances_of(_tree(), "nope") == []


# --- port_connections --------------------------------------------------------


def test_port_connections_returns_list_copy() -> None:
    conns = query.port_connections(_tree(), "top.u_a.u_inner")
    assert [(c.port_name, c.net_expr_text) for c in conns] == [("clk", "clk")]
    conns.append(_conn("ghost", "ghost"))  # mutating the copy is safe
    assert len(query.port_connections(_tree(), "top.u_a.u_inner")) == 1


def test_port_connections_missing_path() -> None:
    assert query.port_connections(_tree(), "top.nowhere") == []


def test_port_connections_at_top_is_empty() -> None:
    # Root node has no own instance — querying it should return [].
    assert query.port_connections(_tree(), "top") == []


# --- source_snippet ----------------------------------------------------------


def test_source_snippet_returns_lines_with_context(tmp_path: Path) -> None:
    src = tmp_path / "a.sv"
    src.write_text("line1\nline2\nmodule m;\nendmodule\nline5\nline6\n")
    loc = SourceLocation(file=str(src), start_line=3, start_column=1, end_line=4)
    snippet = query.source_snippet(loc, context_lines=1)
    # Parse by the " | " separator so the test doesn't sit on the
    # specific column width — which adapts to the largest line number.
    rendered = [line.split(" | ", 1) for line in snippet.splitlines()]
    # context=1 → lines 2..5 inclusive.
    assert rendered == [
        ["2", "line2"],
        ["3", "module m;"],
        ["4", "endmodule"],
        ["5", "line5"],
    ]


def test_source_snippet_includes_line_numbers_by_default(
    tmp_path: Path,
) -> None:
    src = tmp_path / "a.sv"
    src.write_text("a\nb\nc\n")
    loc = SourceLocation(file=str(src), start_line=2)
    snippet = query.source_snippet(loc, context_lines=0)
    assert snippet == "2 | b"


def test_source_snippet_strips_numbers_when_asked(tmp_path: Path) -> None:
    src = tmp_path / "a.sv"
    src.write_text("a\nb\nc\n")
    loc = SourceLocation(file=str(src), start_line=2)
    snippet = query.source_snippet(loc, context_lines=0, with_line_numbers=False)
    assert snippet == "b"


def test_source_snippet_returns_empty_when_loc_is_none() -> None:
    assert query.source_snippet(None) == ""


def test_source_snippet_returns_empty_when_line_unknown() -> None:
    loc = SourceLocation(file="anything", start_line=None)
    assert query.source_snippet(loc) == ""


def test_source_snippet_returns_empty_for_missing_file(tmp_path: Path) -> None:
    loc = SourceLocation(file=str(tmp_path / "does_not_exist.sv"), start_line=1)
    assert query.source_snippet(loc) == ""


def test_source_snippet_clamps_at_file_edges(tmp_path: Path) -> None:
    src = tmp_path / "a.sv"
    src.write_text("only\n")
    loc = SourceLocation(file=str(src), start_line=1)
    # context=10 well past the file — should clamp without raising.
    snippet = query.source_snippet(loc, context_lines=10, with_line_numbers=False)
    assert snippet == "only"
