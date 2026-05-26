"""TB-context clock overlay (rtl-buddy-view #99 / phase 6e).

Loads a hand-authored ``tb_clock_map.json`` and hands the parsed
:class:`TbClockMap` back to the CLI. The CLI's renderer dispatch
merges this with the DUT-side ``clock`` overlay (if loaded) into a
single :class:`DomainMap` so existing per-node clock-contribution
code paths apply unchanged — see
:func:`rtl_buddy_view.tb_clock_map.merge_into_domain_map`.

The TB map is *additive*: when no ``clock`` overlay is loaded, the
TB-side entries still tint nodes outside the DUT subtree. When
both are loaded, the DUT-side map wins for paths inside any DUT
instance (the rule pinned by #99); a stderr warning surfaces any
boundary aliasing.

Separate overlay name (``clock-tb`` rather than reusing ``clock``)
because the schemas + producers are different: ``clock`` consumes
SDC-driven JSON from rtl-buddy-cdc; ``clock-tb`` consumes a
hand-authored TB stimulus map. Two-name surface keeps each
loader's failure mode unambiguous in ``--list-overlays``.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.tb_clock_map import (
    TbClockMap,
    load_tb_clock_map,
)


class ClockTbOverlay:
    """The ``clock-tb`` plugin."""

    name = "clock-tb"
    schema_version = "1.0"

    def load(self, path: Path) -> TbClockMap:
        return load_tb_clock_map(path)

    def join(self, graph: HierNode, annotation: TbClockMap) -> None:
        # The CLI does the merge before any renderer is called, so
        # the per-overlay join hook is a no-op for this plugin.
        return None

    def contribute(self, ctx) -> None:
        return None
