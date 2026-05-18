"""ASCII tree renderer.

Output shape::

    top
    ├── u_fifo : fifo  [clk_a]
    │   ├── u_wr_ptr : ff  [clk_a]
    │   └── u_rd_ptr : ff  [clk_b]  ⚠CDC[clk_a→clk_b]
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

Designed to be terminal-friendly and easy to feed to an LLM.
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.graph import HierNode


def render(
    node: HierNode, out: IO[str], *, domain_map: DomainMap | None = None
) -> None:
    """Render ``node`` and its subtree as ASCII to ``out``."""
    out.write(
        f"{node.module_name}{_clock_suffix(node, domain_map)}"
        f"{_cdc_suffix(node, domain_map)}\n"
    )
    _render_children(node.children, prefix="", out=out, domain_map=domain_map)


def _render_children(
    children: tuple[HierNode, ...],
    *,
    prefix: str,
    out: IO[str],
    domain_map: DomainMap | None,
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
        suffix = _clock_suffix(child, domain_map)
        cdc = _cdc_suffix(child, domain_map)
        out.write(
            f"{prefix}{branch}{inst_name} : {child.module_name}{tag}{suffix}{cdc}\n"
        )
        _render_children(
            child.children, prefix=next_prefix, out=out, domain_map=domain_map
        )


def _clock_suffix(node: HierNode, domain_map: DomainMap | None) -> str:
    """Return the ``  [clk_x]`` suffix or empty string.

    A ``None`` map or an empty (no-SDC) map produces no suffix — the
    renderer falls back to its un-annotated output verbatim. A
    populated map produces ``  [predominant-clock]``; nodes whose
    subtree has no known clock render unannotated even when the map
    is populated (pure-comb subtrees, leaf nodes outside the SDC).
    """
    if domain_map is None or domain_map.is_empty:
        return ""
    clock = domain_map.predominant_clock(node.instance_path)
    if clock is None:
        return ""
    return f"  [{clock}]"


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
