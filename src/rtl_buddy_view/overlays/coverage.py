"""Built-in coverage overlay (Phase 6 — rtl-buddy-view#20).

Thin wrapper over :mod:`rtl_buddy_view.coverage_annotations`
registering the LCOV/Coverview loader on the Phase-4 plugin protocol
so the CLI can dispatch via ``--overlay coverage=PATH``, where PATH
is a combined ``.info`` file, a Coverview-typed directory, or a
``coverview_regression.zip`` archive.

``load`` returns a :class:`CoverageMap` payload the JSON renderer
queries per node by module source range — same direct-lookup pattern
the clock + reset + wave overlays use. The viewer-side
``viewer/src/overlays/coverage.js`` consumes the produced
``node.overlays.coverage`` blocks and paints the heatmap tint; the
desktop tree/dot/mermaid renderers are intentionally untouched in v1
(heatmap rendering is the SPA's job; their output stays byte-identical
with or without this overlay).
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.coverage_annotations import (
    SCHEMA_VERSION,
    CoverageMap,
    load_coverage_map,
)
from rtl_buddy_view.graph import HierNode


class CoverageOverlay:
    """The ``coverage`` plugin."""

    name = "coverage"
    schema_version = SCHEMA_VERSION

    def load(self, path: Path) -> CoverageMap:
        return load_coverage_map(path)

    def join(self, graph: HierNode, annotation: CoverageMap) -> None:
        # Direct-lookup pattern: :meth:`CoverageMap.rollup` is the
        # join surface, called per node by the JSON renderer.
        return None

    def contribute(self, ctx) -> None:
        return None
