"""ASCII tree renderer.

Output shape::

    top
    ├── u_fifo : fifo  [clk_a]
    │   ├── u_wr_ptr : ff  [clk_a, rst_n↓]
    │   └── u_rd_ptr : ff  [clk_b, rst_n↓]  ⚠CDC[clk_a→clk_b]  ⚠RDC[rst_n:async-deassert]
    └── u_ctrl : ctrl

The top line shows just the module name (no instance prefix — at the
root we don't have one). Every other line shows
``<instance-name> : <module-name>``. Blackbox modules render with a
trailing ``(blackbox)`` tag on the module-name slot so reviewers can
see at a glance which definitions weren't found.

When a :class:`rtl_buddy_view.annotations.DomainMap` is supplied:

- Each line is suffixed with the predominant clock of the subtree
  rooted at that node — ``[clk_a]``. Nodes whose subtree has no
  known clock (no flops or all untraceable) render unannotated.
- Flop destinations of an async crossing get an additional
  ``⚠CDC[src_clock→dst_clock]`` suffix so reviewers spot the CDC
  hazards at a glance.

When a :class:`rtl_buddy_view.reset_annotations.ResetDomainMap` is
also supplied (Phase 3):

- Flops with a reset binding get the reset source + polarity arrow
  joined into the same bracket as the clock — ``[clk_a, rst_n↓]``.
  Down-arrow = active-low, up-arrow = active-high; matches the
  rtl-buddy-cdc reset-domain-map ``polarity`` enum.
- Flops in the reset-synchronizer set get a ``✓rstsync`` marker
  appended after the bracket so reviewers can distinguish "vetted
  sync" from "raw reset" at a glance.
- Flops that the producer flagged as the destination of an RDC
  crossing get a trailing ``⚠RDC[<reset>:<kind>]`` marker. Renders
  after any ``⚠CDC[…]`` marker so reviewers see clock issues first
  and reset issues second.

Designed to be terminal-friendly and easy to feed to an LLM.
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.reset_annotations import ResetDomainMap


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
    reset_map: ResetDomainMap | None = None,
) -> None:
    """Render ``node`` and its subtree as ASCII to ``out``."""
    out.write(
        f"{node.module_name}"
        f"{_attr_suffix(node, domain_map, reset_map)}"
        f"{_cdc_suffix(node, domain_map)}"
        f"{_rdc_suffix(node, reset_map)}\n"
    )
    _render_children(
        node.children,
        prefix="",
        out=out,
        domain_map=domain_map,
        reset_map=reset_map,
    )


def _render_children(
    children: tuple[HierNode, ...],
    *,
    prefix: str,
    out: IO[str],
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
) -> None:
    last_idx = len(children) - 1
    for i, child in enumerate(children):
        is_last = i == last_idx
        branch = "└── " if is_last else "├── "
        next_prefix = prefix + ("    " if is_last else "│   ")
        tag = " (blackbox)" if child.is_blackbox else ""
        inst_name = (
            child.instance.name if child.instance is not None else child.module_name
        )
        attr = _attr_suffix(child, domain_map, reset_map)
        cdc = _cdc_suffix(child, domain_map)
        rdc = _rdc_suffix(child, reset_map)
        out.write(
            f"{prefix}{branch}{inst_name} : {child.module_name}{tag}{attr}{cdc}{rdc}\n"
        )
        _render_children(
            child.children,
            prefix=next_prefix,
            out=out,
            domain_map=domain_map,
            reset_map=reset_map,
        )


def _attr_suffix(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
) -> str:
    """Build the combined ``  [clock, reset]`` bracket suffix.

    Empty when neither map contributes anything. When the clock is
    known and the node has a reset binding (i.e. is a flop the
    producer tracked), both appear comma-joined inside one bracket;
    a synchronizer-set member gets a trailing ``  ✓rstsync`` outside
    the bracket so the bracket itself stays focused on attribute
    tags (clock + reset name/polarity).

    Each contributor checks its own collection (rather than the
    map-level ``is_empty``) — a synthetic map with only
    ``reset_synchronizers`` populated still produces the sync marker.
    """
    clock = _clock_label(node, domain_map)
    reset = _reset_label(node, reset_map)
    parts = [p for p in (clock, reset) if p]
    bracket = f"  [{', '.join(parts)}]" if parts else ""

    sync_marker = ""
    if reset_map is not None and node.instance_path in reset_map.synchronizer_paths():
        sync_marker = "  ✓rstsync"

    return bracket + sync_marker


def _clock_label(node: HierNode, domain_map: DomainMap | None) -> str:
    """Return the bare clock name (no surrounding brackets), or ``""``.

    The bracket-construction lives in :func:`_attr_suffix` so we can
    cleanly comma-join with the reset label when both are present.
    """
    if domain_map is None or domain_map.is_empty:
        return ""
    clock = domain_map.predominant_clock(node.instance_path)
    if clock is None:
        return ""
    return clock


def _reset_label(node: HierNode, reset_map: ResetDomainMap | None) -> str:
    """Return ``<reset>↓`` / ``<reset>↑`` for flops with a reset binding.

    Empty when the map is absent, or the node isn't in the producer's
    flop-resets table (pure-comb nodes, blackboxes, flops the producer
    couldn't trace).
    """
    if reset_map is None:
        return ""
    flop = reset_map.flop_reset(node.instance_path)
    if flop is None:
        return ""
    arrow = "↓" if flop.polarity == "low" else "↑"
    return f"{flop.reset}{arrow}"


def _cdc_suffix(node: HierNode, domain_map: DomainMap | None) -> str:
    """Return the ``  ⚠CDC[src→dst, …]`` suffix for crossing destinations.

    Empty when the map is absent / empty, or when no async-crossing
    has this node as its destination flop. Multiple crossings from
    different source clocks collapse to a single suffix that lists
    each source once in alphabetical order.
    """
    if domain_map is None or domain_map.is_empty:
        return ""
    crossings = domain_map.crossings_into(node.instance_path)
    if not crossings:
        return ""
    sources_by_dst: dict[str, set[str]] = {}
    for c in crossings:
        sources_by_dst.setdefault(c.dst_clock, set()).add(c.src_clock)
    parts: list[str] = []
    for dst in sorted(sources_by_dst):
        for src in sorted(sources_by_dst[dst]):
            parts.append(f"{src}→{dst}")
    return "  ⚠CDC[" + ", ".join(parts) + "]"


def _rdc_suffix(node: HierNode, reset_map: ResetDomainMap | None) -> str:
    """Return the ``  ⚠RDC[reset:kind, …]`` suffix for RDC destinations.

    Empty when the map is absent or when no reset-crossing has this
    node as its destination flop. Multiple crossings collapse into a
    single bracket, listed in ``(reset, kind)`` alphabetical order so
    output stays stable across runs.
    """
    if reset_map is None:
        return ""
    crossings = reset_map.crossings_into(node.instance_path)
    if not crossings:
        return ""
    parts = sorted({f"{c.reset}:{c.kind}" for c in crossings})
    return "  ⚠RDC[" + ", ".join(parts) + "]"
