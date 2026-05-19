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
