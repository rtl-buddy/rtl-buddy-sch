"""Query API surface.

A small set of helpers over :class:`rtl_buddy_view.graph.HierNode` and
:class:`rtl_buddy_view.extractor.ModuleTable` that downstream consumers
(rtl-buddy-ai-project, IDE integrations, ad-hoc scripts) can call to
answer common questions without re-walking the graph by hand.

Every result that names a source position returns a
:class:`rtl_buddy_view.extractor.SourceLocation` (or includes one in a
returned dataclass) so callers can jump-to-editor or cite from an LLM.

Phase 1 surface:

- ``find_module(table, name)``
- ``subtree(top, instance_path)``
- ``instances_of(top, module_name)``
- ``port_connections(top, instance_path)``
- ``source_snippet(loc, context_lines=2)``

Implementations land alongside the extractor and graph builder in
[#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
"""

from __future__ import annotations

from rtl_buddy_view.extractor import Module, ModuleTable, SourceLocation
from rtl_buddy_view.graph import HierNode


def find_module(table: ModuleTable, name: str) -> Module | None:
    return table.modules_by_name.get(name)


def subtree(top: HierNode, instance_path: str) -> HierNode | None:
    raise NotImplementedError("Phase 1 stub")


def instances_of(top: HierNode, module_name: str) -> list[HierNode]:
    raise NotImplementedError("Phase 1 stub")


def port_connections(top: HierNode, instance_path: str) -> list:
    raise NotImplementedError("Phase 1 stub")


def source_snippet(loc: SourceLocation, context_lines: int = 2) -> str:
    raise NotImplementedError("Phase 1 stub")
