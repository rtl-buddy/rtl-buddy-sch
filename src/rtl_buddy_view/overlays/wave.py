"""Built-in wave-snapshot overlay (Phase 8 — rtl-buddy-view#21).

Thin wrapper over :mod:`rtl_buddy_view.wave_annotations` registering
the offline waveform-sampler on the Phase-4 plugin protocol so the
CLI can dispatch via ``--overlay wave=foo.vcd:12500ns``.

``load`` returns a :class:`WaveMap` payload the JSON renderer reads
directly — same pattern the clock + reset overlays use. The
viewer-side ``viewer/src/overlays/wave.js`` consumes
``node.overlays.wave.ports[]`` from the produced view.json and paints
port-value badges; the Phase-9 live cache wins on collision but the
offline payload is what shows up in CI artefacts, PR reviews, and any
reproducibility-bound rendering.

CLI form (parsed by :func:`wave_annotations.parse_wave_overlay_spec`):

  --overlay wave=path/to/foo.vcd:12500ns   # explicit time
  --overlay wave=path/to/foo.vcd:end       # last record in file
  --overlay wave=path/to/foo.vcd           # short-form, defaults to end
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.wave_annotations import (
    SCHEMA_VERSION,
    WaveMap,
    load_wave_map,
)


class WaveOverlay:
    """The ``wave`` plugin.

    Name and schema_version stay in lockstep with
    :mod:`rtl_buddy_view.wave_annotations` so a bump on the producer
    side surfaces via ``--list-overlays``.
    """

    name = "wave"
    schema_version = SCHEMA_VERSION

    def load(self, path: Path) -> WaveMap:
        # ``--overlay wave=<spec>`` arrives as a Path whose string
        # form may carry the trailing ``:<time>`` spec. We hand the
        # raw string to the wave loader for splitting + parsing —
        # don't pre-resolve ``path`` via ``Path.exists`` here because
        # the trailing colon-time fragment would make the path lookup
        # fail.
        return load_wave_map(str(path))

    def join(self, graph: HierNode, annotation: WaveMap) -> None:
        # Phase 4 keeps the existing direct-lookup pattern in
        # renderers — :meth:`WaveMap.find_for_port` is the join
        # surface. Hook reserved for future overlays that need to
        # mutate nodes.
        return None

    def contribute(self, ctx) -> None:
        # Same rationale as :meth:`join` — renderers reach into the
        # map directly via the existing helpers.
        return None
