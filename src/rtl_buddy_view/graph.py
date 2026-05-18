"""Hierarchy graph builder.

Pure-data layer. Given a :class:`rtl_buddy_view.extractor.ModuleTable`
and a top-module name, recursively resolves instances into a tree (or
DAG, when the same module is instantiated more than once) of
:class:`HierNode` records. Every node carries its instance path,
referenced module, parameter overrides, and the originating
:class:`Instance` so renderers and the query API can recover both
source-level and structural information.

Phase 1 scope:
- Top-down, single-top resolution
- Multiple instances of the same module (shared definition, distinct
  instance paths)
- Unresolved modules (libraries, blackboxes) flagged via
  :attr:`HierNode.is_blackbox`; not dropped silently
- No generate / parameterized fallback yet — that lands in Phase 2 via
  the slang frontend
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rtl_buddy_view.extractor import Instance, Module, ModuleTable


@dataclass(frozen=True)
class HierNode:
    """One node in the elaborated hierarchy tree.

    ``instance_path`` is the dot-separated absolute path from the top
    (e.g. ``"top.u_fifo.u_wr_ptr"``). The top node has
    ``instance_path == top.name`` and ``instance is None``.
    """

    instance_path: str
    module_name: str
    instance: Instance | None  # None at the top node
    module: Module | None  # None when is_blackbox
    is_blackbox: bool
    children: tuple["HierNode", ...] = field(default_factory=tuple)


def build_hierarchy(table: ModuleTable, top: str) -> HierNode:
    """Build the hierarchy graph rooted at ``top``.

    Phase 1 stub — the public signature is locked so downstream
    renderers and query helpers can be written against it now.
    Implementation lands together with the Verible extractor in
    [#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
    """
    raise NotImplementedError(
        "build_hierarchy is a Phase 1 stub — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/1"
    )
