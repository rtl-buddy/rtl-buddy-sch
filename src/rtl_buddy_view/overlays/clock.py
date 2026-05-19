"""Built-in clock-domain overlay.

Thin wrapper over :mod:`rtl_buddy_view.annotations` (the Phase 2
loader) registering it on the Phase 4 plugin protocol so the CLI
can dispatch via ``--overlay clock=path`` instead of the legacy
``--cdc-annotations`` flag.

``load`` returns the existing :class:`DomainMap` payload verbatim
— renderers already know how to consume it, and that lets Phase 4
land without rewriting any of the per-renderer integration code
from #2.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.annotations import (
    DomainMap,
    load_domain_map,
)
from rtl_buddy_view.graph import HierNode


class ClockOverlay:
    """The ``clock`` plugin.

    Name and schema_version stay in lockstep with the rtl-buddy-cdc
    clock-domain-map (cdc#106) v1.x contract. Bumping the consumer-
    side major is a CLI-visible breaking change and would surface
    via ``--list-overlays``.
    """

    name = "clock"
    schema_version = "1.0"

    def load(self, path: Path) -> DomainMap:
        return load_domain_map(path)

    def join(self, graph: HierNode, annotation: DomainMap) -> None:
        # Phase 4 keeps the existing direct-lookup pattern in
        # renderers — :meth:`DomainMap.predominant_clock` /
        # :meth:`crossings_into` are the join surface. Hook
        # reserved for future overlays that need to mutate nodes.
        return None

    def contribute(self, ctx) -> None:
        # Same rationale as :meth:`join` — renderers reach into
        # the map directly via the existing helpers.
        return None
