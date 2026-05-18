"""Renderers.

All renderers consume a :class:`rtl_buddy_view.graph.HierNode` and
write to a text-mode file-like. Renderers are dumb — they don't make
semantic decisions; they walk the graph and emit one of the supported
formats.

Phase 1 ships ``tree`` (ASCII) and ``dot`` (Graphviz). Phase 2 adds
``mermaid`` and ``json``.
"""
