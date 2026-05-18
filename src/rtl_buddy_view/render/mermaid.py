"""Mermaid flowchart renderer.

Output is a `mermaid <https://mermaid.js.org>`_ ``flowchart TB`` block,
embeddable verbatim in Markdown (GitHub renders it natively in PR
descriptions and READMEs). Output mirrors the dot renderer's
semantics:

- Each instance is a node labeled ``<inst>:<module>`` plus a
  parameter-override line when present.
- Blackbox children render with a dashed stroke + light-yellow fill.
- Edges are labeled with the source port-connection list, truncated
  the same way as dot (matches :data:`render.dot.MAX_EDGE_LABEL_CONNECTIONS`).
- With a :class:`rtl_buddy_view.annotations.DomainMap`, nodes pick
  up a clock tag in their label and a colored fill from the same
  palette as dot. CDC-crossing edges render dashed-red with the
  warning prepended to the label.

Mermaid IDs must be alphanumeric (no dots), so we slugify each
``instance_path``. The mapping is deterministic and reversible by
substring search; instance paths still appear verbatim inside the
labels so the slug is purely a layout-engine concern.
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render.dot import (
    MAX_EDGE_LABEL_CONNECTIONS,
    _format_cdc_summary,
    _palette_color,
)


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
) -> None:
    """Render ``node`` and its subtree as a mermaid flowchart."""
    active_map = domain_map if (domain_map and not domain_map.is_empty) else None
    out.write("```mermaid\n")
    out.write("flowchart TB\n")
    _emit_node(node, out, active_map)
    _emit_edges(node, out, active_map)
    _emit_styles(node, out, active_map)
    out.write("```\n")


# --- nodes -------------------------------------------------------------------


def _emit_node(node: HierNode, out: IO[str], domain_map: DomainMap | None) -> None:
    out.write(f'  {_slug(node.instance_path)}["{_label(node, domain_map)}"]\n')
    for child in node.children:
        _emit_node(child, out, domain_map)


def _label(node: HierNode, domain_map: DomainMap | None) -> str:
    inst_name = node.instance.name if node.instance is not None else node.module_name
    parts = [_escape(inst_name), _escape(node.module_name)]
    if node.instance is not None and node.instance.param_overrides:
        parts.append(_format_param_overrides(node.instance.param_overrides))
    if domain_map is not None:
        clock = domain_map.predominant_clock(node.instance_path)
        if clock is not None:
            parts.append(f"[{_escape(clock)}]")
    if node.is_blackbox:
        parts.append("(blackbox)")
    return "<br/>".join(parts)


def _format_param_overrides(overrides) -> str:
    parts: list[str] = []
    for ov in overrides:
        if ov.param_name is None:
            parts.append(_escape(ov.value_text))
        else:
            parts.append(f".{_escape(ov.param_name)}({_escape(ov.value_text)})")
    return "#(" + ", ".join(parts) + ")"


# --- edges -------------------------------------------------------------------


def _emit_edges(node: HierNode, out: IO[str], domain_map: DomainMap | None) -> None:
    for child in node.children:
        label = _edge_label(child, domain_map)
        cdc = bool(_format_cdc_summary(child, domain_map))
        arrow = "-.->" if cdc else "-->"
        if label:
            out.write(
                f"  {_slug(node.instance_path)} "
                f'{arrow}|"{label}"| '
                f"{_slug(child.instance_path)}\n"
            )
        else:
            out.write(
                f"  {_slug(node.instance_path)} {arrow} {_slug(child.instance_path)}\n"
            )
        _emit_edges(child, out, domain_map)


def _edge_label(child: HierNode, domain_map: DomainMap | None) -> str:
    port_label = ""
    if child.instance is not None and child.instance.port_connections:
        port_label = _format_port_connections(child.instance.port_connections)
    cdc = _format_cdc_summary(child, domain_map)
    if cdc and port_label:
        return f"{cdc}<br/>{port_label}"
    return cdc or port_label


def _format_port_connections(conns) -> str:
    parts: list[str] = []
    rendered = conns
    overflow = 0
    if len(conns) > MAX_EDGE_LABEL_CONNECTIONS:
        keep = MAX_EDGE_LABEL_CONNECTIONS - 1
        rendered = conns[:keep]
        overflow = len(conns) - keep
    for c in rendered:
        if c.port_name is None:
            parts.append(_escape(c.net_expr_text))
        elif not c.net_expr_text:
            parts.append(f".{_escape(c.port_name)}")
        else:
            parts.append(f".{_escape(c.port_name)}({_escape(c.net_expr_text)})")
    if overflow:
        parts.append(f"…(+{overflow} more)")
    return ", ".join(parts)


# --- styles ------------------------------------------------------------------


def _emit_styles(node: HierNode, out: IO[str], domain_map: DomainMap | None) -> None:
    """Emit per-node styles after the topology.

    Mermaid evaluates styles in declaration order; emitting them
    in a single pass keeps the diff stable when a node's clock
    changes without affecting the topology.
    """
    for n in _walk(node):
        attrs: list[str] = []
        if domain_map is not None:
            clock = domain_map.predominant_clock(n.instance_path)
            if clock is not None:
                attrs.append(f"fill:{_palette_color(clock)}")
            elif n.is_blackbox:
                attrs.append("fill:#fff8e0")
        elif n.is_blackbox:
            attrs.append("fill:#fff8e0")
        if n.is_blackbox:
            attrs.append("stroke-dasharray: 3 3")
        # Warning stroke takes precedence on crossing destinations.
        if domain_map is not None and domain_map.crossings_into(n.instance_path):
            attrs.append("stroke:#dc2626")
        if attrs:
            out.write(f"  style {_slug(n.instance_path)} {','.join(attrs)}\n")


def _walk(node: HierNode):
    yield node
    for child in node.children:
        yield from _walk(child)


# --- helpers -----------------------------------------------------------------


def _slug(instance_path: str) -> str:
    """Mermaid-safe ID derived from an instance path.

    Mermaid's flowchart parser rejects dots in node IDs (they're
    syntactic, used for class-level styling), so we convert to
    underscores. The mapping is purely cosmetic — the verbatim
    instance path always appears inside the node label.
    """
    return instance_path.replace(".", "_").replace("-", "_")


def _escape(s: str) -> str:
    """Escape a label fragment for mermaid's quoted-label form.

    Mermaid uses HTML in labels, so we escape ``<`` and ``>`` and
    encode literal newlines as ``<br/>``. Quotes inside labels need
    HTML-encoding (\\" doesn't work in mermaid's parser).
    """
    cleaned = " ".join(s.split())
    return (
        cleaned.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
