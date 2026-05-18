"""Graphviz ``.dot`` renderer.

Module instances as nodes, port connections as labeled edges. The
output is fed to ``dot -Tsvg`` (or any Graphviz consumer) by the
user; we don't shell out to Graphviz ourselves — keeping the renderer
free of toolchain dependencies.

Phase 2 may add per-node coloring for the clock-domain overlay (see
[#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2)).
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.graph import HierNode


def render(node: HierNode, out: IO[str]) -> None:
    """Stub for Phase 1. Signature locked; implementation in [#1]."""
    raise NotImplementedError(
        "Graphviz renderer is a Phase 1 task — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/1"
    )
