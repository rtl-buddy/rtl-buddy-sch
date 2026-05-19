"""Tests for the Phase 3 reset-domain overlay (renderer side).

Parallel to ``test_clock_overlay.py``. Today this covers the tree
renderer (subtask 5 of #3) plus the CLI ``--rdc-annotations`` flag
plumbing (subtask 2). The dot / mermaid / JSON renderer overlays
land in follow-up PRs and will extend this file.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.annotations import (
    Clock,
    DomainMap,
    FlopDomain,
    load_domain_map,
)
from rtl_buddy_view.extractor import Instance
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import HierNode, build_hierarchy
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.render import json_render
from rtl_buddy_view.render import mermaid as mermaid_render
from rtl_buddy_view.render import tree as tree_render
from rtl_buddy_view.reset_annotations import (
    FlopReset,
    ResetCrossing,
    ResetDomainMap,
    ResetSource,
    ResetSynchronizer,
    load_reset_domain_map,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "two_clock_two_reset_design"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


# --- builders ---------------------------------------------------------------


def _node(
    path: str,
    module: str,
    *,
    inst_name: str | None = None,
    children: tuple[HierNode, ...] = (),
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module,
            param_overrides=(),
            port_connections=(),
            location=None,
        )
        if inst_name is not None
        else None
    )
    return HierNode(
        instance_path=path,
        module_name=module,
        instance=inst,
        module=None,
        is_blackbox=False,
        children=children,
    )


def _flop_reset(
    path: str,
    *,
    reset: str = "rst_n",
    polarity: str = "low",
    clock: str | None = None,
) -> FlopReset:
    return FlopReset(
        instance_path=path,
        clock=clock,
        reset=reset,
        reset_kind="port",
        polarity=polarity,  # type: ignore[arg-type]
        type="async",
        location=None,
    )


def _reset_crossing(
    path: str,
    *,
    reset: str = "rst_n",
    kind: str = "async-deassert",
    flop_clock: str | None = None,
) -> ResetCrossing:
    return ResetCrossing(
        instance_path=path,
        kind=kind,  # type: ignore[arg-type]
        flop_clock=flop_clock,
        reset=reset,
        reset_kind="port",
        polarity="low",
        type="async",
        location=None,
    )


def _populated_reset_map(
    *,
    flops: list[FlopReset] | None = None,
    crossings: list[ResetCrossing] | None = None,
    syncs: list[ResetSynchronizer] | None = None,
    sources: list[ResetSource] | None = None,
) -> ResetDomainMap:
    return ResetDomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        reset_sources=tuple(sources or []),
        reset_synchronizers=tuple(syncs or []),
        flop_resets=tuple(flops or []),
        reset_crossings=tuple(crossings or []),
    )


def _populated_clock_map(flops: list[FlopDomain]) -> DomainMap:
    return DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        flop_domains=tuple(flops),
    )


# --- unit: tree renderer reset suffixes -------------------------------------


def test_tree_renders_reset_suffix_alone() -> None:
    """Reset map without a clock map → bare ``[rst_n↓]`` bracket."""
    child = _node("top.u_a", "ff", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    r = _populated_reset_map(flops=[_flop_reset("top.u_a")])
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=r)
    assert buf.getvalue() == "top\n└── u_a : ff  [rst_n↓]\n"


def test_tree_renders_active_high_reset_with_up_arrow() -> None:
    child = _node("top.u_a", "ff", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    r = _populated_reset_map(
        flops=[_flop_reset("top.u_a", reset="rst", polarity="high")]
    )
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=r)
    assert "[rst↑]" in buf.getvalue()


def test_tree_combines_clock_and_reset_in_one_bracket() -> None:
    """Clock + reset both bind to the flop → single comma-joined bracket."""
    child = _node("top.u_a", "ff", inst_name="u_a")
    root = _node("top", "top", children=(child,))
    cm = _populated_clock_map([FlopDomain("top.u_a", "clk_x", None)])
    rm = _populated_reset_map(flops=[_flop_reset("top.u_a")])
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=cm, reset_map=rm)
    # Single bracket, clock first, reset second.
    assert "[clk_x, rst_n↓]" in buf.getvalue()


def test_tree_marks_rdc_destinations() -> None:
    """RDC crossings on a flop surface as ``⚠RDC[reset:kind]`` suffix."""
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_dst")])
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=rm)
    assert "⚠RDC[rst_n:async-deassert]" in buf.getvalue()
    # Top has no crossing into it → no marker on the top line.
    assert buf.getvalue().splitlines()[0] == "top"


def test_tree_collapses_multiple_rdc_crossings_into_one_marker() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    rm = _populated_reset_map(
        crossings=[
            _reset_crossing("top.u_dst", reset="rst_n", kind="async-deassert"),
            _reset_crossing("top.u_dst", reset="por_n", kind="polarity-mismatch"),
        ]
    )
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=rm)
    # Both crossings appear, alphabetically sorted, in one marker.
    assert "⚠RDC[por_n:polarity-mismatch, rst_n:async-deassert]" in buf.getvalue()


def test_tree_marks_synchronizer_membership() -> None:
    """Flops in the reset-synchronizer set get a ``✓rstsync`` marker."""
    sync = _node("top.u_sync", "ff", inst_name="u_sync")
    root = _node("top", "top", children=(sync,))
    rm = _populated_reset_map(
        syncs=[
            ResetSynchronizer(
                instance_path="top.u_sync",
                dest_clock="clk_b",
                async_in="rst_n",
                async_in_kind="port",
                location=None,
            )
        ]
    )
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=rm)
    line = buf.getvalue().splitlines()[1]
    assert line.endswith("  ✓rstsync")


def test_tree_no_reset_suffix_for_unrelated_node() -> None:
    """A pure-comb / blackbox node with no flop_resets entry stays bare."""
    leaf = _node("top.u_b", "comb", inst_name="u_b")
    root = _node("top", "top", children=(leaf,))
    rm = _populated_reset_map(flops=[_flop_reset("top.elsewhere.f1")])
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=rm)
    assert buf.getvalue() == "top\n└── u_b : comb\n"


def test_tree_empty_reset_map_acts_like_no_map() -> None:
    """No-reset payload should produce identical output to ``None``."""
    root = _node("top", "top")
    empty = ResetDomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
    )
    buf_a, buf_b = io.StringIO(), io.StringIO()
    tree_render.render(root, buf_a, reset_map=None)
    tree_render.render(root, buf_b, reset_map=empty)
    assert buf_a.getvalue() == buf_b.getvalue()


def test_tree_cdc_and_rdc_both_render_on_same_flop() -> None:
    """A flop that is both a CDC destination AND an RDC destination
    gets *both* warning suffixes, with CDC first then RDC."""
    from rtl_buddy_view.annotations import Crossing

    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_dst",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="top.src",
            ),
        ),
    )
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_dst")])
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=cm, reset_map=rm)
    output = buf.getvalue()
    # CDC marker appears before RDC marker on the same line.
    cdc_pos = output.index("⚠CDC")
    rdc_pos = output.index("⚠RDC")
    assert cdc_pos < rdc_pos


# --- unit: dot renderer with synthetic graph -------------------------------


def test_dot_renders_reset_bracket_in_node_label() -> None:
    """A flop with a reset binding gets a ``[rst_n↓]`` line in its label."""
    leaf_inst = Instance(
        name="u_a",
        module_name="ff",
        param_overrides=(),
        port_connections=(),
        location=None,
    )
    leaf = HierNode(
        instance_path="top.u_mid.u_a",
        module_name="ff",
        instance=leaf_inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    mid = _node("top.u_mid", "mid", inst_name="u_mid", children=(leaf,))
    root = _node("top", "top", children=(mid,))
    rm = _populated_reset_map(flops=[_flop_reset("top.u_mid.u_a")])
    buf = io.StringIO()
    dot_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    assert "[rst_n↓]" in text


def test_dot_renders_active_high_reset_with_up_arrow() -> None:
    leaf_inst = Instance(
        name="u_a",
        module_name="ff",
        param_overrides=(),
        port_connections=(),
        location=None,
    )
    leaf = HierNode(
        instance_path="top.u_mid.u_a",
        module_name="ff",
        instance=leaf_inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    mid = _node("top.u_mid", "mid", inst_name="u_mid", children=(leaf,))
    root = _node("top", "top", children=(mid,))
    rm = _populated_reset_map(
        flops=[_flop_reset("top.u_mid.u_a", reset="rst", polarity="high")]
    )
    buf = io.StringIO()
    dot_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    assert "[rst↑]" in text


def test_dot_marks_synchronizer_with_teal_outline() -> None:
    """A flop in the reset-synchronizer set gets a teal outline + ✓rstsync."""
    leaf_inst = Instance(
        name="u_sync",
        module_name="ff",
        param_overrides=(),
        port_connections=(),
        location=None,
    )
    leaf = HierNode(
        instance_path="top.u_mid.u_sync",
        module_name="ff",
        instance=leaf_inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    mid = _node("top.u_mid", "mid", inst_name="u_mid", children=(leaf,))
    root = _node("top", "top", children=(mid,))
    rm = _populated_reset_map(
        syncs=[
            ResetSynchronizer(
                instance_path="top.u_mid.u_sync",
                dest_clock="clk_b",
                async_in="rst_n",
                async_in_kind="port",
                location=None,
            )
        ]
    )
    buf = io.StringIO()
    dot_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    assert "✓rstsync" in text
    # Teal outline on the sync node.
    sync_line = next(
        ln for ln in text.splitlines() if ln.lstrip().startswith('"top.u_mid.u_sync"')
    )
    assert "#0d9488" in sync_line


def test_dot_emits_dashed_orange_rdc_edge() -> None:
    """An RDC crossing into a child renders the parent→child edge dashed-orange."""
    leaf_inst = Instance(
        name="u_dst",
        module_name="ff",
        param_overrides=(),
        port_connections=(),
        location=None,
    )
    leaf = HierNode(
        instance_path="top.u_mid.u_dst",
        module_name="ff",
        instance=leaf_inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    mid = _node("top.u_mid", "mid", inst_name="u_mid", children=(leaf,))
    root = _node("top", "top", children=(mid,))
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_mid.u_dst")])
    buf = io.StringIO()
    dot_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    rdc_edge = next(
        ln for ln in text.splitlines() if '"top.u_mid" -> "top.u_mid.u_dst"' in ln
    )
    assert "⚠RDC: rst_n:async-deassert" in rdc_edge
    assert "#ea580c" in rdc_edge
    assert 'style="dashed"' in rdc_edge


def test_dot_emits_rdc_arrow_from_top_reset_port() -> None:
    """An RDC where the reset name matches a top input port gets a dashed-orange
    arrow from the port anchor to the destination child (mirrors CDC arrows)."""
    from rtl_buddy_view.extractor import Module, Port

    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    top_mod = Module(
        name="top",
        ports=(
            Port(name="clk", direction="input", type_text=None, location=None),
            Port(name="rst_n", direction="input", type_text=None, location=None),
        ),
        parameters=(),
        instances=(),
        location=None,
    )
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=top_mod,
        is_blackbox=False,
        children=(dst,),
    )
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_dst")])
    buf = io.StringIO()
    dot_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    assert "⚠RDC: rst_n:async-deassert" in text
    assert '"_in_rst_n" -> "top.u_dst"' in text
    assert "#ea580c" in text


def test_dot_cdc_red_takes_precedence_on_dual_issue_edge() -> None:
    """When the same edge is both CDC and RDC, the edge stays CDC-red and
    the RDC marker appears as an additional label line."""
    from rtl_buddy_view.annotations import Crossing

    leaf_inst = Instance(
        name="u_dst",
        module_name="ff",
        param_overrides=(),
        port_connections=(),
        location=None,
    )
    leaf = HierNode(
        instance_path="top.u_mid.u_dst",
        module_name="ff",
        instance=leaf_inst,
        module=None,
        is_blackbox=False,
        children=(),
    )
    mid = _node("top.u_mid", "mid", inst_name="u_mid", children=(leaf,))
    root = _node("top", "top", children=(mid,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_mid.u_dst",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="top.src",
            ),
        ),
    )
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_mid.u_dst")])
    buf = io.StringIO()
    dot_render.render(root, buf, domain_map=cm, reset_map=rm)
    text = buf.getvalue()
    edge_line = next(
        ln for ln in text.splitlines() if '"top.u_mid" -> "top.u_mid.u_dst"' in ln
    )
    # Edge is red (CDC), not orange.
    assert "#dc2626" in edge_line
    assert "#ea580c" not in edge_line
    # Both markers appear in the label.
    assert "⚠CDC: clk_a→clk_b" in edge_line
    assert "⚠RDC: rst_n:async-deassert" in edge_line


def test_dot_no_reset_map_renders_unchanged() -> None:
    """``reset_map=None`` produces output byte-identical to the legacy
    Phase-2 dot rendering on the same input."""
    leaf = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(leaf,))
    buf_none, buf_phase2 = io.StringIO(), io.StringIO()
    dot_render.render(root, buf_none, reset_map=None)
    dot_render.render(root, buf_phase2)  # no reset_map kwarg at all
    assert buf_none.getvalue() == buf_phase2.getvalue()


# --- unit: mermaid renderer with synthetic graph ----------------------------


def test_mermaid_renders_reset_bracket_in_node_label() -> None:
    leaf = _node("top.u_a", "ff", inst_name="u_a")
    root = _node("top", "top", children=(leaf,))
    rm = _populated_reset_map(flops=[_flop_reset("top.u_a")])
    buf = io.StringIO()
    mermaid_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    assert "[rst_n↓]" in text


def test_mermaid_marks_rdc_destination_with_dashed_arrow_and_orange_stroke() -> None:
    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_dst")])
    buf = io.StringIO()
    mermaid_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    # Edge label carries the RDC marker.
    assert "⚠RDC: rst_n:async-deassert" in text
    # Edge to u_dst is dashed (``-.->``).
    assert "-.->" in text
    # Destination node carries the orange stroke.
    assert "stroke:#ea580c" in text


def test_mermaid_synchronizer_gets_teal_stroke() -> None:
    leaf = _node("top.u_sync", "ff", inst_name="u_sync")
    root = _node("top", "top", children=(leaf,))
    rm = _populated_reset_map(
        syncs=[
            ResetSynchronizer(
                instance_path="top.u_sync",
                dest_clock="clk_b",
                async_in="rst_n",
                async_in_kind="port",
                location=None,
            )
        ]
    )
    buf = io.StringIO()
    mermaid_render.render(root, buf, reset_map=rm)
    text = buf.getvalue()
    assert "✓rstsync" in text
    assert "stroke:#0d9488" in text


def test_mermaid_cdc_takes_precedence_over_rdc_stroke() -> None:
    """Dual-issue node: CDC red wins on stroke; RDC label still appears."""
    from rtl_buddy_view.annotations import Crossing

    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    cm = DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="yosys",
        clocks=(Clock(name="clk_a", period=10.0, source="create_clock", ports=()),),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_dst",
                min_hops=0,
                width=1,
                async_per_sdc=True,
                src_flop="top.src",
            ),
        ),
    )
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_dst")])
    buf = io.StringIO()
    mermaid_render.render(root, buf, domain_map=cm, reset_map=rm)
    text = buf.getvalue()
    # CDC red stroke wins; no orange stroke on the same node.
    assert "stroke:#dc2626" in text
    # Both markers in the edge label.
    assert "⚠CDC: clk_a→clk_b" in text
    assert "⚠RDC: rst_n:async-deassert" in text


def test_mermaid_no_reset_map_renders_unchanged() -> None:
    leaf = _node("top.u_a", "child", inst_name="u_a")
    root = _node("top", "top", children=(leaf,))
    buf_none, buf_phase2 = io.StringIO(), io.StringIO()
    mermaid_render.render(root, buf_none, reset_map=None)
    mermaid_render.render(root, buf_phase2)
    assert buf_none.getvalue() == buf_phase2.getvalue()


# --- unit: JSON renderer with synthetic graph -------------------------------


def test_json_renders_reset_fields_on_each_node() -> None:
    import json

    leaf = _node("top.u_a", "ff", inst_name="u_a")
    root = _node("top", "top", children=(leaf,))
    rm = _populated_reset_map(flops=[_flop_reset("top.u_a")])
    buf = io.StringIO()
    json_render.render(root, buf, reset_map=rm)
    payload = json.loads(buf.getvalue())
    nodes_by_path = {n["instance_path"]: n for n in payload["nodes"]}
    flop = nodes_by_path["top.u_a"]
    assert flop["reset"] == {
        "name": "rst_n",
        "polarity": "low",
        "type": "async",
        "kind": "port",
    }
    assert flop["reset_crossings_in"] == []
    assert flop["is_reset_synchronizer"] is False
    # Top has no reset binding → null + empty
    assert nodes_by_path["top"]["reset"] is None


def test_json_renders_reset_crossings_in() -> None:
    import json

    dst = _node("top.u_dst", "ff", inst_name="u_dst")
    root = _node("top", "top", children=(dst,))
    rm = _populated_reset_map(crossings=[_reset_crossing("top.u_dst")])
    buf = io.StringIO()
    json_render.render(root, buf, reset_map=rm)
    payload = json.loads(buf.getvalue())
    nodes_by_path = {n["instance_path"]: n for n in payload["nodes"]}
    crossings = nodes_by_path["top.u_dst"]["reset_crossings_in"]
    assert len(crossings) == 1
    assert crossings[0] == {
        "reset": "rst_n",
        "kind": "async-deassert",
        "flop_clock": None,
        "polarity": "low",
        "type": "async",
        "reset_kind": "port",
    }


def test_json_marks_reset_synchronizer_flag() -> None:
    import json

    sync = _node("top.u_sync", "ff", inst_name="u_sync")
    root = _node("top", "top", children=(sync,))
    rm = _populated_reset_map(
        syncs=[
            ResetSynchronizer(
                instance_path="top.u_sync",
                dest_clock="clk_b",
                async_in="rst_n",
                async_in_kind="port",
                location=None,
            )
        ]
    )
    buf = io.StringIO()
    json_render.render(root, buf, reset_map=rm)
    payload = json.loads(buf.getvalue())
    nodes_by_path = {n["instance_path"]: n for n in payload["nodes"]}
    assert nodes_by_path["top.u_sync"]["is_reset_synchronizer"] is True
    assert nodes_by_path["top"]["is_reset_synchronizer"] is False


def test_json_no_reset_map_emits_null_and_false_defaults() -> None:
    """``reset_map=None`` still emits the reset-shaped fields with
    graceful-degradation values, so downstream consumers can rely on
    their presence."""
    import json

    leaf = _node("top.u_a", "ff", inst_name="u_a")
    root = _node("top", "top", children=(leaf,))
    buf = io.StringIO()
    json_render.render(root, buf)
    payload = json.loads(buf.getvalue())
    for n in payload["nodes"]:
        assert n["reset"] is None
        assert n["reset_crossings_in"] == []
        assert n["is_reset_synchronizer"] is False


# --- integration: real Verible parse + reset map ----------------------------


pytestmark_integration = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


@pytest.fixture
def integration_root() -> HierNode:
    table = parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )
    return build_hierarchy(table, "top")


@pytestmark_integration
def test_integration_tree_with_reset_only(integration_root: HierNode) -> None:
    rm = load_reset_domain_map(FIXTURE_DIR / "reset_map.json")
    buf = io.StringIO()
    tree_render.render(integration_root, buf, reset_map=rm)
    output = buf.getvalue()
    assert "u_wr_ptr : ff  [rst_n↓]" in output
    assert "u_rd_ptr : ff  [rst_n↓]  ⚠RDC[rst_n:async-deassert]" in output
    # u_sync isn't in flop_resets (the reset_map.json only tracks the
    # data flops, not the synchroniser's own internal cell) but it IS
    # in reset_synchronizers — so it picks up the ✓rstsync marker
    # without a reset bracket.
    assert "u_sync : ff  ✓rstsync" in output


@pytestmark_integration
def test_integration_tree_with_combined_clock_and_reset(
    integration_root: HierNode,
) -> None:
    """The headline Phase 3 acceptance shape: both maps overlaid.

    Exercises the combined-bracket path (clock + reset name) plus
    the RDC marker on the destination flop.
    """
    cm = load_domain_map(FIXTURE_DIR / "clock_map.json")
    rm = load_reset_domain_map(FIXTURE_DIR / "reset_map.json")
    buf = io.StringIO()
    tree_render.render(integration_root, buf, domain_map=cm, reset_map=rm)
    output = buf.getvalue()
    assert "u_wr_ptr : ff  [clk_a, rst_n↓]" in output
    # u_rd_ptr is the destination of the RDC crossing; no CDC marker
    # in this fixture (clock_map.json has crossings: []).
    assert "u_rd_ptr : ff  [clk_b, rst_n↓]  ⚠RDC[rst_n:async-deassert]" in output


@pytestmark_integration
def test_integration_dot_with_combined_clock_and_reset(
    integration_root: HierNode,
) -> None:
    """Headline Phase 3 dot output: clock + reset overlay together."""
    cm = load_domain_map(FIXTURE_DIR / "clock_map.json")
    rm = load_reset_domain_map(FIXTURE_DIR / "reset_map.json")
    buf = io.StringIO()
    dot_render.render(integration_root, buf, domain_map=cm, reset_map=rm)
    output = buf.getvalue()
    # Reset bracket appears on the flop nodes.
    assert "[rst_n↓]" in output
    # RDC edge to u_rd_ptr renders dashed-orange.
    rd_edge = next(
        line
        for line in output.splitlines()
        if "top.u_fifo.u_rd_ptr" in line and "->" in line and "_in_" not in line
    )
    assert "⚠RDC: rst_n:async-deassert" in rd_edge
    assert "#ea580c" in rd_edge
    # ``_in_rst_n → top.u_fifo.u_rd_ptr`` doesn't fire here because the
    # destination is a grandchild of top (not a direct child); the
    # marker on the u_fifo→u_rd_ptr edge is the surface form for that
    # nesting depth. Direct-child reset arrows are unit-tested in
    # ``test_dot_emits_rdc_arrow_from_top_reset_port``.
    # The reset-synchroniser instance (top.u_rstgen.u_sync) gets the
    # teal outline + ✓rstsync.
    sync_line = next(
        line
        for line in output.splitlines()
        if '"top.u_rstgen.u_sync"' in line and "label=" in line
    )
    assert "✓rstsync" in sync_line
    assert "#0d9488" in sync_line


@pytestmark_integration
def test_integration_two_separate_resets_with_rdc() -> None:
    """Fixture (c) from issue #3 subtask 8: two clocks, two separate resets,
    one async-deassert RDC where rst_a_n is sampled by clk_b on u_b.
    """
    fix = Path(__file__).parent / "fixtures" / "two_reset_with_rdc"
    table = parse_to_modules(parse_filelist(fix / "files.f"), frontend=Frontend.verible)
    root = build_hierarchy(table, "top")
    cm = load_domain_map(fix / "clock_map.json")
    rm = load_reset_domain_map(fix / "reset_map.json")
    buf = io.StringIO()
    tree_render.render(root, buf, domain_map=cm, reset_map=rm)
    output = buf.getvalue()
    # u_a — clean (no RDC), reset by rst_a_n in its own clock domain.
    assert "u_a : ff  [clk_a, rst_a_n↓]" in output
    # u_b — rst_a_n crosses into clk_b. RDC marker present.
    assert "u_b : ff  [clk_b, rst_a_n↓]  ⚠RDC[rst_a_n:async-deassert]" in output


@pytestmark_integration
def test_integration_reset_synchronizer_chain() -> None:
    """Fixture (d) from issue #3 subtask 8: a two-stage reset
    synchronizer chain. Both sync flops are in ``reset_synchronizers``
    and pick up the ``✓rstsync`` marker; u_data is reset by the
    synchronizer output (``top.u_sync_stage2``) without an RDC
    crossing, exercising the "vetted sync, no warning" path.
    """
    fix = Path(__file__).parent / "fixtures" / "reset_synchronizer_chain"
    table = parse_to_modules(parse_filelist(fix / "files.f"), frontend=Frontend.verible)
    root = build_hierarchy(table, "top")
    rm = load_reset_domain_map(fix / "reset_map.json")
    buf = io.StringIO()
    tree_render.render(root, buf, reset_map=rm)
    output = buf.getvalue()
    # Both sync stages get the marker.
    assert "u_sync_stage1 : ff  ✓rstsync" in output
    assert "u_sync_stage2 : ff  ✓rstsync" in output
    # u_data reset by the synchronizer output, no RDC marker.
    assert "u_data : ff  [top.u_sync_stage2↓]" in output
    assert "⚠RDC" not in output


@pytestmark_integration
def test_integration_mermaid_with_combined_clock_and_reset(
    integration_root: HierNode,
) -> None:
    cm = load_domain_map(FIXTURE_DIR / "clock_map.json")
    rm = load_reset_domain_map(FIXTURE_DIR / "reset_map.json")
    buf = io.StringIO()
    mermaid_render.render(integration_root, buf, domain_map=cm, reset_map=rm)
    text = buf.getvalue()
    assert "[clk_a]" in text and "[clk_b]" in text
    assert "[rst_n↓]" in text
    assert "⚠RDC: rst_n:async-deassert" in text
    # u_rd_ptr (RDC dst, no CDC on this fixture) takes orange stroke.
    assert "stroke:#ea580c" in text
    # u_sync gets teal.
    assert "stroke:#0d9488" in text


@pytestmark_integration
def test_integration_json_with_combined_clock_and_reset(
    integration_root: HierNode,
) -> None:
    import json

    cm = load_domain_map(FIXTURE_DIR / "clock_map.json")
    rm = load_reset_domain_map(FIXTURE_DIR / "reset_map.json")
    buf = io.StringIO()
    json_render.render(integration_root, buf, domain_map=cm, reset_map=rm)
    payload = json.loads(buf.getvalue())
    nodes_by_path = {n["instance_path"]: n for n in payload["nodes"]}

    rd_ptr = nodes_by_path["top.u_fifo.u_rd_ptr"]
    assert rd_ptr["clock"] == "clk_b"
    assert rd_ptr["reset"]["name"] == "rst_n"
    assert rd_ptr["reset"]["polarity"] == "low"
    assert len(rd_ptr["reset_crossings_in"]) == 1
    assert rd_ptr["reset_crossings_in"][0]["kind"] == "async-deassert"
    assert rd_ptr["is_reset_synchronizer"] is False

    sync = nodes_by_path["top.u_rstgen.u_sync"]
    assert sync["is_reset_synchronizer"] is True


@pytestmark_integration
def test_cli_passes_rdc_annotations_through() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "top",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--format",
            "tree",
            "--rdc-annotations",
            str(FIXTURE_DIR / "reset_map.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "[rst_n↓]" in result.stdout
    assert "⚠RDC[rst_n:async-deassert]" in result.stdout


@pytestmark_integration
def test_cli_passes_both_annotations_through() -> None:
    """Both flags composable in a single invocation."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "top",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--format",
            "tree",
            "--cdc-annotations",
            str(FIXTURE_DIR / "clock_map.json"),
            "--rdc-annotations",
            str(FIXTURE_DIR / "reset_map.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "[clk_a, rst_n↓]" in result.stdout
    assert "[clk_b, rst_n↓]" in result.stdout
    assert "⚠RDC[rst_n:async-deassert]" in result.stdout


def _normalize_paths(text: str) -> str:
    """Strip the absolute-path prefix from JSON ``location.file`` values.

    Verible reports source locations with absolute filesystem paths, so
    the raw golden differs between developer machines and CI runners.
    The renderer ships the path verbatim by design (consumers like
    ``rb hier`` want the unambiguous resolved path); we only normalise
    inside the golden harness so the *content* of the location is what
    gets pinned, not the filesystem layout of the machine that ran the
    test.

    Strips everything up to and including ``/tests/fixtures/`` —
    leaves the fixture-relative path verbatim.
    """
    import re

    return re.sub(
        r'"file":\s*"[^"]*?/tests/fixtures/',
        '"file": "tests/fixtures/',
        text,
    )


@pytestmark_integration
@pytest.mark.parametrize(
    "renderer_name,renderer_fn,golden_name",
    [
        ("tree", tree_render.render, "tree.txt"),
        ("dot", dot_render.render, "hierarchy.dot"),
        ("mermaid", mermaid_render.render, "hierarchy.mmd"),
        ("json", json_render.render, "hierarchy.json"),
    ],
)
def test_golden_output_per_renderer(
    integration_root: HierNode,
    renderer_name: str,
    renderer_fn,
    golden_name: str,
) -> None:
    """Subtask 9 of #3: byte-for-byte golden regression per renderer.

    The fixtures under ``two_clock_two_reset_design/goldens/`` are the
    pinned acceptance shape — both annotation flags supplied, the
    headline two-clock / two-reset / one-RDC scenario. Cosmetic
    changes (palette tweaks, label rephrasing, edge-attribute order)
    will fail this test; regenerate the goldens deliberately with
    ``uv run python tests/regen_goldens.py`` (see that script's
    comment block for the full command).

    The JSON golden's ``location.file`` values carry absolute paths
    from Verible's extractor; we normalise both sides through
    :func:`_normalize_paths` so the golden pins *content* across
    machines without baking in a specific filesystem layout.
    """
    cm = load_domain_map(FIXTURE_DIR / "clock_map.json")
    rm = load_reset_domain_map(FIXTURE_DIR / "reset_map.json")
    buf = io.StringIO()
    renderer_fn(integration_root, buf, domain_map=cm, reset_map=rm)
    actual = _normalize_paths(buf.getvalue())
    expected = _normalize_paths((FIXTURE_DIR / "goldens" / golden_name).read_text())
    assert actual == expected, (
        f"{renderer_name} golden drifted — regenerate with "
        f"tests/regen_goldens.py if the change is intentional."
    )


def test_cli_rejects_malformed_rdc_annotations(tmp_path: Path) -> None:
    """A bad map surfaces ``rdc-annotations: …`` (not ``cdc-annotations:``)."""
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": "9.9"}')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "top",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--rdc-annotations",
            str(bad),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "rdc-annotations:" in result.stderr
    # And specifically not the cdc-annotations error prefix.
    assert "cdc-annotations:" not in result.stderr
