"""Built-in physical overlay (Phase 7b — rtl-buddy/rtl-buddy-sch#22).

Thin wrapper over :mod:`rtl_buddy_view.phys_annotations` registering
the ``phys-model.json`` / ``phys-manifest.json`` reader on the
Phase-4 plugin protocol, so the CLI dispatches via
``--overlay phys=PATH`` where PATH is a manifest (followed through
its ``model`` pointer), a model, or the artefact directory holding
either.

``load`` returns a :class:`~rtl_buddy_view.phys_annotations.PhysModel`.
Unlike the clock / reset / coverage built-ins, the *join* is not a
per-node lookup each renderer can make on its own: the power channel
attributes every leaf row to its nearest enclosing scope and then
rolls the parents up, which is one walk of the whole hierarchy. So
the CLI makes it once, via
:func:`rtl_buddy_view.phys_annotations.join_hierarchy`, against the
hint-rewritten root, and hands the resulting
:class:`~rtl_buddy_view.phys_annotations.PhysJoin` to whichever
renderer runs — the JSON renderer copies each node's block into
``overlays.phys``, the ASCII tree reads the suffix off it, and dot /
mermaid ignore it entirely.

The producer is ``rb synth`` / ``rb power``
(rtl-buddy/rtl_buddy#558, delivering rtl-buddy/rtl_buddy#114); the
schema this consumes and the two joins it makes are documented in
``docs/phys-overlay.md``.
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.phys_annotations import (
    SCHEMA_VERSION,
    PhysModel,
    load_phys_model,
)


class PhysOverlay:
    """The ``phys`` plugin."""

    name = "phys"
    schema_version = SCHEMA_VERSION

    def load(self, path: Path) -> PhysModel:
        return load_phys_model(path)

    def join(self, graph: HierNode, annotation: PhysModel) -> None:
        """No-op, like every other built-in — but for a new reason.

        The others are no-ops because their renderers look their
        payload up per node. This one's join is real work, it just
        cannot land *here*: the protocol's hook returns nothing and
        ``HierNode`` is frozen, so there is nowhere to put the
        product. :func:`~rtl_buddy_view.phys_annotations.join_hierarchy`
        is the join, the CLI calls it once, and the
        :class:`~rtl_buddy_view.phys_annotations.PhysJoin` it returns
        is what the renderers read. Widening the protocol's return
        type for one built-in would be a change to the third-party
        plugin surface for no gain to a plugin.
        """
        return None

    def contribute(self, ctx) -> None:
        return None
