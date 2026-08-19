"""Built-in diagram-hint overlay (epic #159, phase 1).

Thin wrapper over :mod:`rtl_buddy_view.hints` registering the sidecar
loader on the Phase-4 plugin protocol, so the JSON equivalent of the
in-source ``// rbsch:`` pragmas loads through the same
``--overlay name=path`` surface every other artefact does::

    rtl-buddy-view --top top --filelist files.f \\
        --overlay hints=docs/top.hints.json

The sidecar exists for source the author can't edit — vendor or
generated IP — and wins over an in-source pragma on conflict, since
pointing at it on the command line is the more deliberate act.

Unlike the clock / reset / coverage overlays, this one's payload is
consumed *before* rendering: the CLI merges it with the scanned
pragmas and hands the result to
:func:`rtl_buddy_view.hints.apply_hints`, which rewrites the
hierarchy graph. Every renderer therefore sees the hinted graph
without knowing hints exist.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.hints import (
    SIDECAR_SCHEMA_VERSION,
    HintMap,
    load_hint_sidecar,
)


class HintsOverlay:
    """The ``hints`` plugin."""

    name = "hints"
    schema_version = SIDECAR_SCHEMA_VERSION

    def load(self, path: Path) -> HintMap:
        return load_hint_sidecar(path)

    def join(self, graph: HierNode, annotation: HintMap) -> None:
        # The graph rewrite is :func:`rtl_buddy_view.hints.apply_hints`,
        # which the CLI runs once over the merged (pragma + sidecar)
        # map — a per-overlay join here would only see half of it.
        return None

    def contribute(self, ctx) -> None:
        # Hints change what exists, not how it is drawn; nothing to
        # contribute at render time.
        return None
