"""Landscape grid packing in the cluster-tree DOT (rtl-buddy-sch#138).

The hierarchy canvas renders the DOT the Python producer embeds in
``view.json``. In cluster-tree mode a scope's children carry no sibling
edges, so dot gave them all one rank and — under ``rankdir="LR"`` — one
column: the scope came out as tall as it had children and one box wide.

These tests pin the replacement rule: a per-scope column count chosen
from the scope's own estimated geometry, realised as invisible chain
edges. They build :class:`HierNode`\\s directly, so no Verible is
needed; the one test that measures a real bounding box shells out to
Graphviz and skips when ``dot`` isn't installed.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess

import pytest

from rtl_buddy_view.extractor import Instance, Module, ModuleTable, Port
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.hints import HintMap, InstanceHints
from rtl_buddy_view.render import dot as dot_render

#: Marks the grid chain. The ``rbsch`` ``rank=`` hint chain also emits
#: invisible edges, but carries ``weight=8`` — keep the two apart.
CHAIN = "[style=invis];"


def _leaf(parent_path: str, name: str, module: str) -> HierNode:
    return HierNode(
        instance_path=f"{parent_path}.{name}",
        module_name=module,
        instance=Instance(
            name=name,
            module_name=module,
            param_overrides=(),
            port_connections=(),
            location=None,
        ),
        module=None,
        is_blackbox=False,
        children=(),
    )


def _top(children: tuple[HierNode, ...], *, ports: tuple[Port, ...] = ()) -> HierNode:
    return HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=Module(
            name="top",
            ports=ports,
            parameters=(),
            instances=(),
            location=None,
        ),
        is_blackbox=False,
        children=children,
    )


def _wide_top(count: int = 8) -> HierNode:
    """A top whose children are many same-sized leaves — the bug's shape."""
    return _top(tuple(_leaf("top", f"u_blk{i}", "blk") for i in range(count)))


def _render(node: HierNode, **kwargs) -> str:
    buf = io.StringIO()
    dot_render.render(node, buf, **kwargs)
    return buf.getvalue()


def _chain_edges(dot_text: str) -> list[tuple[str, str]]:
    """The ``(source, target)`` pairs of the grid's invisible chain."""
    pairs = []
    for line in dot_text.splitlines():
        if CHAIN not in line:
            continue
        head, _, tail = line.strip().partition(" -> ")
        pairs.append((head.strip().strip('"'), tail.split(" [")[0].strip().strip('"')))
    return pairs


# --- the rule ------------------------------------------------------


def test_wide_scope_is_packed_into_several_columns() -> None:
    """Eight sibling leaves must not render as an eight-high column."""
    plan = _plan_for(_wide_top())
    assert len(plan["top"].columns) > 1
    # Every child lands in exactly one column, none invented.
    dealt = [c.instance_path for col in plan["top"].columns for c in col]
    assert sorted(dealt) == sorted(f"top.u_blk{i}" for i in range(8))


def test_landscape_scope_keeps_its_single_column() -> None:
    """A scope that is already wider than tall is left alone.

    One child cannot be improved by wrapping, and the tie-break rule
    ("ties go to the smaller count") has to keep that case at one
    column rather than inventing a degenerate grid.
    """
    plan = _plan_for(_top((_leaf("top", "u_only", "blk"),)))
    assert len(plan["top"].columns) == 1


def test_packing_is_deterministic() -> None:
    """Two renders of the same tree are byte-identical."""
    top = _wide_top()
    assert _render(top, as_cluster_tree=True) == _render(top, as_cluster_tree=True)


def test_chain_edges_are_invisible_and_cross_column_only() -> None:
    """The chain only ever links a column to the *next* one.

    An edge inside a column would stack its members on separate ranks,
    which is the column layout the rule exists to break up.
    """
    top = _wide_top()
    plan = _plan_for(top)
    column_of = {
        child.instance_path: index
        for index, column in enumerate(plan["top"].columns)
        for child in column
    }
    edges = _chain_edges(_render(top, as_cluster_tree=True))
    assert edges
    for source, target in edges:
        assert column_of[source] + 1 == column_of[target]


def test_column_is_chained_from_its_deepest_member() -> None:
    """A multi-rank child must not be overlapped by the next column.

    dot splices a cluster's internal ranks into the parent's rank
    sequence, so a child that is itself two columns wide spans two of
    the parent's columns. Chaining off its anchor would overlap them;
    the chain therefore starts at the child's *last* rank.
    """
    # u_deep is wide enough inside to be packed into >1 column itself.
    inner = tuple(_leaf("top.u_deep", f"u_c{i}", "cell") for i in range(6))
    deep = HierNode(
        instance_path="top.u_deep",
        module_name="deep",
        instance=Instance(
            name="u_deep",
            module_name="deep",
            param_overrides=(),
            port_connections=(),
            location=None,
        ),
        module=None,
        is_blackbox=False,
        children=inner,
    )
    top = _top((deep, _leaf("top", "u_after", "blk")))
    plan = _plan_for(top)
    assert len(plan["top.u_deep"].columns) > 1
    if len(plan["top"].columns) > 1 and deep in plan["top"].columns[0]:
        # The tail of the column holding u_deep is a node inside its
        # last inner column, never the cluster anchor itself.
        assert plan["top"].tails[0].startswith("top.u_deep.")


# --- what the rule must not touch ----------------------------------


def test_standalone_dot_is_not_packed() -> None:
    """``--format dot`` keeps the box-and-arrow tree it always had.

    That shape already has parent→child edges to rank by, so there is
    no single column to break up — and its golden is a public surface.
    """
    assert CHAIN not in _render(_wide_top())


def test_block_diagram_is_not_packed() -> None:
    """Sibling dataflow decides the block diagram's ranks, not us."""
    table = ModuleTable(
        modules_by_name={
            "top": Module(
                name="top",
                ports=(),
                parameters=(),
                instances=(),
                location=None,
            )
        }
    )
    assert CHAIN not in _render(_wide_top(), block_diagram=True, module_table=table)


def test_layout_hints_win_over_the_packer() -> None:
    """An author who wrote ``rbsch: rank=`` has laid the scope out."""
    top = _wide_top(4)
    hints = HintMap(
        instances={
            ("top", "u_blk0"): InstanceHints(rank=1),
            ("top", "u_blk1"): InstanceHints(rank=2),
        }
    )
    dot_text = _render(top, as_cluster_tree=True, hints=hints)
    assert CHAIN not in dot_text
    # The hint chain itself still fires, weighted.
    assert "[style=invis, weight=8]" in dot_text


def test_children_are_chained_off_the_first_port_anchor() -> None:
    """A child with no port edge must not sit in the port column.

    ``rank=source`` pins the port anchors to the minimum rank; a child
    with no incoming edge of its own would settle there too and stack
    in among the port labels — the same failure one level out.
    """
    ports = (
        Port(name="clk", direction="input", type_text=None, location=None),
        Port(name="q", direction="output", type_text=None, location=None),
    )
    top = _top((_leaf("top", "u_a", "blk"), _leaf("top", "u_b", "blk")), ports=ports)
    edges = _chain_edges(_render(top, as_cluster_tree=True))
    assert ("_in_clk", "top.u_a") in edges
    assert ("_in_clk", "top.u_b") in edges


# --- the measured outcome ------------------------------------------


def _plan_for(top: HierNode) -> dict[str, dot_render._ScopePack]:
    inputs, outputs = dot_render._split_ports(
        top, module_table=None, bundle_interfaces=False
    )
    in_w, in_h = dot_render._port_anchor_extent([p.name for p in inputs])
    out_w, out_h = dot_render._port_anchor_extent([p.name for p in outputs])
    frame = dot_render._PackFrame(
        side_width=in_w + out_w,
        height_floor=max(in_h, out_h),
        pad_width=dot_render._TOP_FRAME_PAD_X,
        pad_height=dot_render._TOP_FRAME_PAD_Y,
        head=f"_in_{inputs[0].name}" if inputs else None,
    )
    return dot_render._build_pack_plan(top, None, None, None, frame)


def _aspect(dot_text: str) -> float:
    """Width / height of what Graphviz actually lays the DOT out as."""
    proc = subprocess.run(
        ["dot", "-Tjson"], input=dot_text, capture_output=True, text=True, check=True
    )
    x0, y0, x1, y1 = (float(v) for v in json.loads(proc.stdout)["bb"].split(","))
    return (x1 - x0) / (y1 - y0)


@pytest.mark.skipif(
    shutil.which("dot") is None, reason="Graphviz not installed on this machine"
)
def test_packing_turns_a_portrait_scope_landscape() -> None:
    """The end-to-end claim of #138, measured rather than asserted.

    Stripping the chain edges from the emitted DOT reproduces exactly
    the pre-#138 layout, so the same fixture gives both numbers and the
    comparison can't drift apart from the renderer.
    """
    packed = _render(_wide_top(9), as_cluster_tree=True)
    unpacked = "\n".join(line for line in packed.splitlines() if CHAIN not in line)
    before = _aspect(unpacked)
    after = _aspect(packed)
    assert before < 1.0, f"fixture no longer reproduces the bug (aspect {before:.2f})"
    assert after > before
    # The viewport this targets is ~1.7 (16:9 minus the sidebar).
    assert 1.2 <= after <= 2.4, f"packed aspect {after:.2f} outside the target band"


@pytest.mark.skipif(
    shutil.which("dot") is None, reason="Graphviz not installed on this machine"
)
def test_packed_dot_lays_out_without_graphviz_warnings() -> None:
    """Zero Graphviz warnings is this renderer's bar; the chain keeps it."""
    proc = subprocess.run(
        ["dot", "-Tsvg"],
        input=_render(_wide_top(9), as_cluster_tree=True),
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stderr.strip() == ""
