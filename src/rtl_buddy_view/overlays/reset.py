"""Built-in reset-domain overlay.

Thin wrapper over :mod:`rtl_buddy_view.reset_annotations` (the
Phase 3 loader) registering it on the Phase 4 plugin protocol so
the CLI can dispatch via ``--overlay reset=path`` instead of the
legacy ``--rdc-annotations`` flag.

Naming follows the issue's resolution: the overlay describes the
*domain* (``reset``), not the analysis (``rdc``); RDC is the
crossing-analysis side-effect that this overlay surfaces, just
like CDC was for the clock overlay.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.reset_annotations import (
    ResetDomainMap,
    load_reset_domain_map,
)


class ResetOverlay:
    """The ``reset`` plugin."""

    name = "reset"
    schema_version = "1.0"

    def load(self, path: Path) -> ResetDomainMap:
        return load_reset_domain_map(path)

    def join(self, graph: HierNode, annotation: ResetDomainMap) -> None:
        # See :class:`ClockOverlay.join`. Direct-lookup pattern via
        # :meth:`ResetDomainMap.flop_reset` / :meth:`crossings_into`
        # / :meth:`synchronizer_paths` is the join surface today.
        return None

    def contribute(self, ctx) -> None:
        return None
