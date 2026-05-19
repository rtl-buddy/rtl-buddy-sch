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
    _format_rdc_summary,
    _palette_color,
)
from rtl_buddy_view.reset_annotations import ResetDomainMap

#: RDC accent stroke colour. Mirrors the dot renderer's ``_RDC_COLOR``
#: so mermaid + dot output read as the same warning under the same
#: design + maps.
_RDC_STROKE = "#ea580c"
#: Reset-synchronizer outline. Mirrors ``dot._RSTSYNC_OUTLINE``.
_RSTSYNC_STROKE = "#0d9488"


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
    reset_map: ResetDomainMap | None = None,
) -> None:
    """Render ``node`` and its subtree as a mermaid flowchart.

    With a :class:`rtl_buddy_view.reset_annotations.ResetDomainMap`
    (Phase 3), nodes pick up a ``[rst_n↓]`` line in their label, the
    reset-synchroniser set gets a teal stroke, and RDC-crossing edges
    render dashed-orange with the warning prepended to the label.
    Dual-issue (CDC + RDC) edges stay CDC-red on the stroke; the RDC
    marker rides on the label only — same precedence as the dot
    renderer.
    """
    active_map = domain_map if (domain_map and not domain_map.is_empty) else None
    active_reset_map = reset_map if reset_map is not None else None
    out.write("```mermaid\n")
    out.write("flowchart TB\n")
    _emit_node(node, out, active_map, active_reset_map)
    _emit_edges(node, out, active_map, active_reset_map)
    _emit_styles(node, out, active_map, active_reset_map)
    out.write("```\n")


# --- nodes -------------------------------------------------------------------


def _emit_node(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> None:
    out.write(
        f'  {_slug(node.instance_path)}["{_label(node, domain_map, reset_map)}"]\n'
    )
    for child in node.children:
        _emit_node(child, out, domain_map, reset_map)


def _label(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> str:
    inst_name = node.instance.name if node.instance is not None else node.module_name
    parts = [_escape(inst_name), _escape(node.module_name)]
    if node.instance is not None and node.instance.param_overrides:
        parts.append(_format_param_overrides(node.instance.param_overrides))
    if domain_map is not None:
        clock = domain_map.predominant_clock(node.instance_path)
        if clock is not None:
            parts.append(f"[{_escape(clock)}]")
    if reset_map is not None:
        flop = reset_map.flop_reset(node.instance_path)
        if flop is not None:
            arrow = "↓" if flop.polarity == "low" else "↑"
            parts.append(f"[{_escape(flop.reset)}{arrow}]")
        if node.instance_path in reset_map.synchronizer_paths():
            parts.append("✓rstsync")
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


def _emit_edges(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> None:
    for child in node.children:
        label = _edge_label(child, domain_map, reset_map)
        cdc = bool(_format_cdc_summary(child, domain_map))
        rdc = bool(_format_rdc_summary(child, reset_map))
        # Dashed arrow for either warning class; the *colour* differs
        # via per-edge linkStyle in :func:`_emit_styles` since mermaid
        # doesn't accept inline edge colours.
        arrow = "-.->" if (cdc or rdc) else "-->"
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
        _emit_edges(child, out, domain_map, reset_map)


def _edge_label(
    child: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> str:
    port_label = ""
    if child.instance is not None and child.instance.port_connections:
        port_label = _format_port_connections(child.instance.port_connections)
    cdc = _format_cdc_summary(child, domain_map)
    rdc = _format_rdc_summary(child, reset_map) if reset_map is not None else ""
    parts = [p for p in (cdc, rdc, port_label) if p]
    return "<br/>".join(parts)


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


def _emit_styles(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> None:
    """Emit per-node styles after the topology.

    Mermaid evaluates styles in declaration order; emitting them in a
    single pass keeps the diff stable when a node's clock changes
    without affecting the topology.

    Stroke precedence on dual-issue nodes mirrors the dot renderer:
    CDC red > RDC orange > sync teal. The fill comes from the clock
    palette in all three cases so the warning rides on the stroke
    without losing the clock-coloring cue.
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
        # Warning stroke ladder: CDC > RDC > sync. CDC wins on a
        # dual-issue node so reviewers see the silicon-failing hazard
        # first; the RDC warning still appears on the edge / label.
        if domain_map is not None and domain_map.crossings_into(n.instance_path):
            attrs.append("stroke:#dc2626")
        elif reset_map is not None and reset_map.crossings_into(n.instance_path):
            attrs.append(f"stroke:{_RDC_STROKE}")
        elif (
            reset_map is not None and n.instance_path in reset_map.synchronizer_paths()
        ):
            attrs.append(f"stroke:{_RSTSYNC_STROKE}")
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
