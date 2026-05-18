"""Query API surface.

A small set of helpers over :class:`rtl_buddy_view.graph.HierNode` and
:class:`rtl_buddy_view.extractor.ModuleTable` that downstream consumers
(rtl-buddy-ai-project, IDE integrations, ad-hoc scripts) can call to
answer common questions without re-walking the graph by hand.

Every result that names a source position returns a
:class:`rtl_buddy_view.extractor.SourceLocation` (or includes one in a
returned dataclass) so callers can jump-to-editor or cite from an LLM.

Phase 1 surface:

- :func:`find_module` — module by name
- :func:`subtree` — navigate to an instance by dot-separated path
- :func:`walk` — depth-first iteration over all hierarchy nodes
- :func:`instances_of` — every instance of a given module across the tree
- :func:`port_connections` — port-connection list for one instance
- :func:`source_snippet` — text slice around a :class:`SourceLocation`
  with configurable context lines, suitable for LLM citation
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from rtl_buddy_view.extractor import (
    Module,
    ModuleTable,
    PortConnection,
    SourceLocation,
)
from rtl_buddy_view.graph import HierNode


def find_module(table: ModuleTable, name: str) -> Module | None:
    """Return the module definition by name, or ``None`` if absent."""
    return table.modules_by_name.get(name)


def walk(top: HierNode) -> Iterator[HierNode]:
    """Depth-first iteration over ``top`` and all descendants."""
    yield top
    for child in top.children:
        yield from walk(child)


def subtree(top: HierNode, instance_path: str) -> HierNode | None:
    """Return the hierarchy node at ``instance_path`` or ``None``.

    ``instance_path`` is the dot-separated absolute path from the
    top (e.g. ``"counter.u_ff"``). Matches against
    :attr:`HierNode.instance_path` exactly — no glob, no
    case-folding. Leading/trailing dots are stripped so callers can
    pass ``".u_ff"`` or ``"counter.u_ff."`` without surprise.
    """
    needle = instance_path.strip(".")
    for node in walk(top):
        if node.instance_path == needle:
            return node
    return None


def instances_of(top: HierNode, module_name: str) -> list[HierNode]:
    """All hierarchy nodes whose ``module_name`` matches.

    Includes blackbox nodes. Returns a list (not an iterator) because
    callers almost always want ``len(...)`` and stable ordering;
    ordering is the depth-first walk order so multi-instance results
    stay readable.
    """
    return [n for n in walk(top) if n.module_name == module_name]


def port_connections(top: HierNode, instance_path: str) -> list[PortConnection]:
    """Port connections on the instance at ``instance_path``.

    Returns ``[]`` if the path doesn't resolve, the node is the top
    (which has no own instantiation), or the instance has no
    connections. The list is a copy — callers can sort or filter
    without mutating the underlying tuple.
    """
    node = subtree(top, instance_path)
    if node is None or node.instance is None:
        return []
    return list(node.instance.port_connections)


def source_snippet(
    loc: SourceLocation | None,
    context_lines: int = 2,
    *,
    with_line_numbers: bool = True,
) -> str:
    """Return the source text covering ``loc`` with surrounding context.

    Designed for LLM citation: the snippet is wrapped with ``±N``
    lines of context (default 2) and line-number-prefixed by default
    so a model has the file:line anchor inline. Returns an empty
    string when:

    - ``loc`` is ``None``
    - ``loc.start_line`` is missing (frontend didn't resolve an
      anchor)
    - the file can't be read (deleted between extract and query)

    No exception in any of those cases — the snippet is best-effort
    context, not a correctness path.
    """
    if loc is None or loc.start_line is None:
        return ""
    try:
        text = Path(loc.file).read_text()
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    start_line = loc.start_line
    end_line = loc.end_line if loc.end_line is not None else loc.start_line
    first = max(1, start_line - context_lines)
    last = min(len(lines), end_line + context_lines)
    selected = lines[first - 1 : last]
    if not with_line_numbers:
        return "\n".join(selected)
    width = len(str(last))
    return "\n".join(
        f"{first + i:>{width}} | {line}" for i, line in enumerate(selected)
    )
