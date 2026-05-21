"""Built-in axi-perf overlay (Phase 11 — #60).

Loads an ``axi-perf.json`` artifact produced by
[rtl-buddy/rtl-buddy-axi-profiler](https://github.com/rtl-buddy/rtl-buddy-axi-profiler)
into an :class:`AxiPerfMap`, registered on the Phase 4 plugin
protocol so the CLI can dispatch via ``--overlay axi-perf=path``.

This first slice lands the load + protocol registration. Renderer
integration (per-edge ``overlays.axi_perf`` in view.json, ASCII
edge annotations in tree mode, ``DOT`` edge styling for backpressure
heatmap) lands in follow-up PRs to #60 — those touch the renderer
chain and require a small amendment to the overlay framework so
``Overlay.contribute(ctx)`` can write to ``ctx.edges[i]``.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.axi_perf_annotations import (
    AxiPerfMap,
    load_axi_perf_map,
)
from rtl_buddy_view.graph import HierNode


class AxiPerfOverlay:
    """The ``axi-perf`` plugin.

    Name and schema_version stay in lockstep with the
    rtl-buddy-axi-profiler axi-perf.json v1.x contract. Bumping the
    consumer-side major is a CLI-visible breaking change and would
    surface via ``--list-overlays``.
    """

    name = "axi-perf"
    schema_version = "1.0"

    def load(self, path: Path) -> AxiPerfMap:
        return load_axi_perf_map(path)

    def join(self, graph: HierNode, annotation: AxiPerfMap) -> None:
        # Same rationale as the clock / reset overlays: renderers
        # reach into the map directly via the lookup methods
        # (:meth:`AxiPerfMap.bundle_at_edge`) at render time. Hook
        # reserved for future per-node annotations.
        return None

    def contribute(self, ctx) -> None:
        # Renderer integration lands in a follow-up to #60 — see the
        # module docstring. Phase 11's first slice is the loader +
        # protocol registration; renderer contributions arrive once
        # the per-edge overlay framework seam is added.
        return None
