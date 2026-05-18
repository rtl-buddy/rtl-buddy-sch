"""Graphviz ``.dot`` renderer.

Module instances as nodes, parent→child relationships as edges. Each
node's label shows ``<instance-name>\\n<module-name>`` plus a third
line of named parameter overrides when any are present
(``#(.WIDTH(16), .DEPTH(32))``). Blackbox modules — anything the
extractor could not resolve to a :class:`rtl_buddy_view.extractor.Module`
— render with a dashed border so reviewers see at a glance which
definitions are missing.

The output is fed to ``dot -Tsvg`` (or any Graphviz consumer) by the
user; we don't shell out to Graphviz ourselves — keeping the renderer
free of toolchain dependencies. Output is deterministic given the
graph; goldens diff cleanly across runs.

Phase 2 will overlay clock-domain coloring on this same renderer
(see [#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2)).
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.extractor import ParameterOverride
from rtl_buddy_view.graph import HierNode


def render(node: HierNode, out: IO[str]) -> None:
    """Render ``node`` and its subtree as a Graphviz ``.dot`` digraph."""
    out.write("digraph hierarchy {\n")
    out.write('  rankdir="TB";\n')
    out.write('  node [shape=box, style="rounded,filled", fillcolor="#f5f5f5"];\n')
    out.write("\n")
    _emit_node(node, out)
    _emit_edges(node, out)
    out.write("}\n")


def _emit_node(node: HierNode, out: IO[str]) -> None:
    label = _label_for(node)
    attrs = [f'label="{label}"']
    if node.is_blackbox:
        attrs.append('style="rounded,filled,dashed"')
        attrs.append('fillcolor="#fff8e0"')
    out.write(f'  "{node.instance_path}" [{", ".join(attrs)}];\n')
    for child in node.children:
        _emit_node(child, out)


def _emit_edges(node: HierNode, out: IO[str]) -> None:
    for child in node.children:
        out.write(f'  "{node.instance_path}" -> "{child.instance_path}";\n')
        _emit_edges(child, out)


def _label_for(node: HierNode) -> str:
    inst_name = node.instance.name if node.instance is not None else node.module_name
    lines = [_escape(inst_name), _escape(node.module_name)]
    if node.instance is not None and node.instance.param_overrides:
        lines.append(_format_param_overrides(node.instance.param_overrides))
    if node.is_blackbox:
        lines.append("(blackbox)")
    return r"\n".join(lines)


def _format_param_overrides(overrides: tuple[ParameterOverride, ...]) -> str:
    parts: list[str] = []
    for ov in overrides:
        if ov.param_name is None:
            # Positional overrides aren't extracted today; if they
            # ever surface here, render as bare value so the diagram
            # still reflects them.
            parts.append(_escape(ov.value_text))
        else:
            parts.append(f".{_escape(ov.param_name)}({_escape(ov.value_text)})")
    return "#(" + ", ".join(parts) + ")"


def _escape(s: str) -> str:
    """Escape a string for use inside a Graphviz quoted label.

    Graphviz labels need backslashes and double-quotes escaped; we
    also strip embedded newlines and collapse runs of whitespace so a
    multi-line type slice (rare but possible from the source-slice
    extractor) renders on one line within its label segment.
    """
    cleaned = " ".join(s.split())
    return cleaned.replace("\\", "\\\\").replace('"', '\\"')
