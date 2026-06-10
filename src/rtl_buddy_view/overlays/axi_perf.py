"""Built-in axi-perf overlay (Phase 11 — #60).

Loads an ``axi-perf.json`` artifact produced by
[rtl-buddy/rtl-buddy-axi-profiler](https://github.com/rtl-buddy/rtl-buddy-axi-profiler)
into an :class:`AxiPerfMap`, registered on the Phase 4 plugin
protocol so the CLI can dispatch via ``--overlay axi-perf=path``.

The shipped design surfaces AXI performance in a dedicated **AXI
tab** in the viewer (``viewer/src/components/AxiPerfView.vue``), not
as per-edge styling on the hierarchy. #69 dropped the original
per-edge stroke/colour plan in favour of that tab, and #114 unified
the bundle→hierarchy join onto the interface-port / tb-top
mechanism. Renderers therefore read the :class:`AxiPerfMap` directly
— ``json_render`` emits per-node / per-edge ``overlays.axi_perf``
plus a top-level ``axi_perf`` block, and the viewer renders the tab
from it. ``contribute()`` and ``join()`` are deliberate no-ops: the
edge-contribution framework amendment once mooted for #60 was never
needed. See ``docs/axi-perf-overlay.md``.
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
        # Deliberate no-op. Perf is surfaced by the viewer's AXI tab
        # and by json_render reading the AxiPerfMap directly (see the
        # module docstring); the design pivoted away from edge styling
        # (#69), so there is no edge contribution to make.
        return None
