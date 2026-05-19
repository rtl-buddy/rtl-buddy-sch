"""Tests for the synth-flop-path → source-instance resolver.

Hand-authored ``DomainMap`` + ``HierNode`` fixtures keep these tests
independent of any frontend. The renderer-level effect of resolution
is covered end-to-end by ``test_clock_overlay.py``.
"""

from __future__ import annotations

from rtl_buddy_view._flop_resolver import resolve_for_hierarchy
from rtl_buddy_view.annotations import (
    Clock,
    Crossing,
    DomainMap,
    FlopDomain,
)
from rtl_buddy_view.extractor import Instance, Module, SourceLocation
from rtl_buddy_view.graph import HierNode


def _node(
    path: str,
    module_name: str,
    *,
    inst_name: str | None = None,
    module_loc: SourceLocation | None = None,
    children: tuple[HierNode, ...] = (),
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name=module_name,
            param_overrides=(),
            port_connections=(),
            location=None,
        )
        if inst_name is not None
        else None
    )
    module = Module(
        name=module_name,
        ports=(),
        parameters=(),
        instances=(),
        location=module_loc,
    )
    return HierNode(
        instance_path=path,
        module_name=module_name,
        instance=inst,
        module=module,
        is_blackbox=False,
        children=children,
    )


def _map(
    *,
    flop_domains: tuple[FlopDomain, ...] = (),
    crossings: tuple[Crossing, ...] = (),
) -> DomainMap:
    return DomainMap(
        schema_version="1.0",
        generator_name="test",
        generator_version="0",
        design_top="top",
        design_frontend="slang",
        clocks=(
            Clock(name="clk_a", period=10.0, source="create_clock", ports=()),
            Clock(name="clk_b", period=8.0, source="create_clock", ports=()),
        ),
        flop_domains=flop_domains,
        crossings=crossings,
    )


# --- path-prefix resolution -----------------------------------------------


def test_strips_synth_suffix_to_enclosing_instance() -> None:
    """``top.u_sync.$slang$sdff$3`` resolves to ``top.u_sync``."""
    sync = _node("top.u_sync", "ip_cdc_sync", inst_name="u_sync")
    root = _node("top", "top", children=(sync,))
    m = _map(
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_sync.$slang$sdff$3",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop="top.$slang$sdff$1",
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    assert len(out.crossings) == 1
    assert out.crossings[0].dst_flop == "top.u_sync"
    assert out.crossings[0].src_flop == "top"


def test_exact_match_is_idempotent() -> None:
    """When the dst_flop already names a HierNode, the map is unchanged."""
    leaf = _node("top.u_leaf", "ff", inst_name="u_leaf")
    root = _node("top", "top", children=(leaf,))
    m = _map(
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_leaf",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop=None,
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    assert out.crossings[0].dst_flop == "top.u_leaf"


def test_unresolvable_crossing_is_dropped() -> None:
    """A crossing whose dst_flop has no source-instance prefix is silently dropped."""
    root = _node("top", "top")  # no children
    m = _map(
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="some.totally.unrelated.flop",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop=None,
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    assert out.crossings == ()


def test_src_flop_resolution_failure_keeps_original_text() -> None:
    """Resolution failures on ``src_flop`` are not fatal — render logic
    keys on ``dst_flop``; the source text is informational."""
    sync = _node("top.u_sync", "ip_cdc_sync", inst_name="u_sync")
    root = _node("top", "top", children=(sync,))
    m = _map(
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_sync.$slang$sdff$3",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop="external.flop.that.does.not.resolve",
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    assert out.crossings[0].dst_flop == "top.u_sync"
    # src_flop kept verbatim because the renderer doesn't dispatch on it.
    assert out.crossings[0].src_flop == "external.flop.that.does.not.resolve"


# --- location cross-check + flattened-synthesis fallback -------------------


def test_location_confirms_path_walk() -> None:
    """Path-walk + matching location: instance is attributed."""
    sync_loc = SourceLocation(file="ip_cdc_sync.sv", start_line=18, end_line=27)
    sync = _node(
        "top.u_sync",
        "ip_cdc_sync",
        inst_name="u_sync",
        module_loc=sync_loc,
    )
    root = _node("top", "top", children=(sync,))
    flop_loc = SourceLocation(file="ip_cdc_sync.sv", start_line=22, end_line=22)
    m = _map(
        flop_domains=(
            FlopDomain(
                instance_path="top.u_sync.$slang$sdff$3",
                clock="clk_b",
                location=flop_loc,
            ),
        ),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_sync.$slang$sdff$3",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop=None,
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    assert out.crossings[0].dst_flop == "top.u_sync"


def test_location_disambiguates_when_path_lies() -> None:
    """Path-walk lands on a wrong instance because the synth flattened
    a module; location scan picks the right one.

    Set up: synth path ``top.u_outer.$slang$sdff$3`` — path-walk picks
    ``top.u_outer``. But the flop's location is in ``inner.sv``, and
    ``top.u_outer.u_inner`` is a real instance whose module range
    contains the location. The resolver must reject the path-walk
    answer and fall back to the location-scan answer.
    """
    inner_loc = SourceLocation(file="inner.sv", start_line=10, end_line=20)
    outer_loc = SourceLocation(file="outer.sv", start_line=1, end_line=50)
    inner = _node(
        "top.u_outer.u_inner", "inner", inst_name="u_inner", module_loc=inner_loc
    )
    outer = _node(
        "top.u_outer",
        "outer",
        inst_name="u_outer",
        module_loc=outer_loc,
        children=(inner,),
    )
    root = _node("top", "top", children=(outer,))
    flop_loc = SourceLocation(file="inner.sv", start_line=15, end_line=15)
    m = _map(
        flop_domains=(
            FlopDomain(
                # Note: path says u_outer (synth flattened u_inner away
                # in its naming), but location says inner.sv.
                instance_path="top.u_outer.$slang$sdff$3",
                clock="clk_b",
                location=flop_loc,
            ),
        ),
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_outer.$slang$sdff$3",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop=None,
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    # Location wins over the lying path: attribute to the inner instance.
    assert out.crossings[0].dst_flop == "top.u_outer.u_inner"


def test_path_walk_used_when_location_missing() -> None:
    """Maps without flop locations still get path-walk resolution.

    Strictly better than the exact-match status quo: the markers fire,
    they just lack the location cross-check robustness.
    """
    sync = _node("top.u_sync", "ip_cdc_sync", inst_name="u_sync")
    root = _node("top", "top", children=(sync,))
    m = _map(
        # No flop_domains → no locations to look up.
        crossings=(
            Crossing(
                src_clock="clk_a",
                dst_clock="clk_b",
                dst_flop="top.u_sync.$slang$sdff$3",
                async_per_sdc=True,
                min_hops=0,
                width=1,
                src_flop=None,
            ),
        ),
    )
    out = resolve_for_hierarchy(m, root)
    assert out.crossings[0].dst_flop == "top.u_sync"
