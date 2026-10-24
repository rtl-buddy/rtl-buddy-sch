"""Built-in ``clock-colors`` overlay (rtl-buddy-sch#196).

Pins chosen colours on named clocks, for a document set whose figures
must agree that ``s_clk`` is blue::

    rtl-buddy-view --top top --filelist files.f --format dot \\
        --overlay clock=domain_map.json \\
        --overlay clock-colors=clock-colors.json

    {"schema_version": "1.0",
     "clocks": {"s_clk": "blue", "f_clk": "#fde68a"}}

Like ``hints``, the payload is consumed by the CLI rather than through
``join`` / ``contribute``: it is merged with any ``// rbsch: clock
color=…`` pragma colours (the overlay wins) and handed to the dot and
mermaid renderers, which deal every other clock a distinct swatch
(:mod:`rtl_buddy_view.clock_colors`). Without a ``clock`` overlay there
are no clocks to colour, and it has no effect.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.clock_colors import (
    SCHEMA_VERSION,
    ClockColorMap,
    load_clock_colors,
)
from rtl_buddy_view.graph import HierNode


class ClockColorsOverlay:
    """The ``clock-colors`` plugin."""

    name = "clock-colors"
    schema_version = SCHEMA_VERSION

    def load(self, path: Path) -> ClockColorMap:
        return load_clock_colors(path)

    def join(self, graph: HierNode, annotation: ClockColorMap) -> None:
        # Colours are figure-wide, not per node; the CLI hands the map
        # to the renderers directly.
        return None

    def contribute(self, ctx) -> None:
        return None
