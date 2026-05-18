"""Graphviz ``.dot`` renderer.

Module instances as nodes, parent→child relationships as edges. Each
node's label shows ``<instance-name>\\n<module-name>`` plus a third
line of named parameter overrides when any are present
(``#(.WIDTH(16), .DEPTH(32))``). Blackbox modules — anything the
extractor could not resolve to a :class:`rtl_buddy_view.extractor.Module`
— render with a dashed border so reviewers see at a glance which
definitions are missing.

Edges are labeled with the named port connections taken verbatim
from the source — ``.clk(clk), .q(q[0])`` — truncated with ``…`` when
an instance has more than :data:`MAX_EDGE_LABEL_CONNECTIONS`
connections so a wide bus interface doesn't blow out the diagram.
Truncation is purely visual; the underlying
:class:`rtl_buddy_view.extractor.Instance.port_connections` tuple
always carries the full list for downstream consumers.

When a :class:`rtl_buddy_view.annotations.DomainMap` is supplied,
each node's fill color is set from a deterministic clock-domain
palette so subtrees in the same clock land in the same color
across runs. Pass ``with_legend=True`` to emit a small subgraph
listing each clock and its swatch.

The output is fed to ``dot -Tsvg`` (or any Graphviz consumer) by the
user; we don't shell out to Graphviz ourselves — keeping the renderer
free of toolchain dependencies. Output is deterministic given the
graph; goldens diff cleanly across runs.
"""

from __future__ import annotations

import hashlib
from typing import IO

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.extractor import ParameterOverride, PortConnection
from rtl_buddy_view.graph import HierNode

MAX_EDGE_LABEL_CONNECTIONS = 6
"""Cap on port connections rendered inline on an edge label.

Beyond this we render the first ``MAX_EDGE_LABEL_CONNECTIONS - 1``
entries followed by ``…(+N more)`` so the diagram stays readable on
wide bus interfaces. The data model itself is untouched."""

# A small palette of distinct pastel fills tuned for legibility on
# both light and dark backgrounds. Hash-mapped to clock names so the
# same clock always lands in the same color across runs — handy when
# diffing diagrams across SDC edits. Pinned to seven entries: more
# would dilute legibility, and an RTL with eight or more distinct
# clocks is realistically going to need a hierarchical overlay
# anyway (filed as a Phase 3 follow-up).
_CLOCK_PALETTE: tuple[str, ...] = (
    "#dbeafe",  # blue
    "#dcfce7",  # green
    "#fef9c3",  # yellow
    "#fce7f3",  # pink
    "#ede9fe",  # purple
    "#fed7aa",  # orange
    "#cffafe",  # cyan
)

_DEFAULT_FILL = "#f5f5f5"
_BLACKBOX_FILL = "#fff8e0"


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
    with_legend: bool = False,
) -> None:
    """Render ``node`` and its subtree as a Graphviz ``.dot`` digraph."""
    active_map = domain_map if (domain_map and not domain_map.is_empty) else None
    out.write("digraph hierarchy {\n")
    out.write('  rankdir="TB";\n')
    out.write(
        f'  node [shape=box, style="rounded,filled", fillcolor="{_DEFAULT_FILL}"];\n'
    )
    out.write("\n")
    _emit_node(node, out, active_map)
    _emit_edges(node, out)
    if with_legend and active_map is not None:
        _emit_legend(active_map, out)
    out.write("}\n")


def _emit_node(node: HierNode, out: IO[str], domain_map: DomainMap | None) -> None:
    label = _label_for(node, domain_map)
    attrs = [f'label="{label}"']
    fill = _fill_for(node, domain_map)
    if node.is_blackbox:
        attrs.append('style="rounded,filled,dashed"')
        attrs.append(f'fillcolor="{fill}"')
    elif fill != _DEFAULT_FILL:
        attrs.append(f'fillcolor="{fill}"')
    out.write(f'  "{node.instance_path}" [{", ".join(attrs)}];\n')
    for child in node.children:
        _emit_node(child, out, domain_map)


def _fill_for(node: HierNode, domain_map: DomainMap | None) -> str:
    """Pick the node's fill color.

    Annotations off → blackbox-yellow for blackboxes, default gray
    otherwise. Annotations on → clock-keyed pastel from the palette
    when the subtree has a predominant clock, else fall back to the
    annotations-off behavior (so pure-comb and untraceable nodes
    stay neutral).
    """
    if domain_map is None:
        return _BLACKBOX_FILL if node.is_blackbox else _DEFAULT_FILL
    clock = domain_map.predominant_clock(node.instance_path)
    if clock is None:
        return _BLACKBOX_FILL if node.is_blackbox else _DEFAULT_FILL
    return _palette_color(clock)


def _palette_color(clock_name: str) -> str:
    """Deterministic clock→palette mapping via stable digest.

    Using a hash (not Python's ``hash()``) keeps colors stable across
    Python processes and PYTHONHASHSEED values — important when the
    output is checked into golden files or feeding visual diff tools.
    """
    digest = hashlib.sha256(clock_name.encode("utf-8")).digest()
    return _CLOCK_PALETTE[digest[0] % len(_CLOCK_PALETTE)]


def _emit_legend(domain_map: DomainMap, out: IO[str]) -> None:
    """Emit a clock→swatch legend as a side subgraph.

    Each entry is a small filled box labeled with the clock name and
    period (when available). Subgraph stays untethered from the
    hierarchy nodes so dot's layout engine treats it as a separate
    rank below the main diagram.
    """
    out.write("\n  subgraph cluster_clock_legend {\n")
    out.write('    label="Clocks";\n')
    out.write('    style="dashed";\n')
    out.write('    rank="sink";\n')
    for clock in sorted(domain_map.clocks, key=lambda c: c.name):
        fill = _palette_color(clock.name)
        if clock.period is not None:
            label = f"{clock.name}\\n{clock.period}"
        else:
            label = clock.name
        out.write(
            f'    "_legend_{clock.name}" '
            f'[label="{label}", shape=box, style="rounded,filled", '
            f'fillcolor="{fill}"];\n'
        )
    out.write("  }\n")


def _emit_edges(node: HierNode, out: IO[str]) -> None:
    for child in node.children:
        attrs = ""
        if child.instance is not None and child.instance.port_connections:
            label = _format_port_connections(child.instance.port_connections)
            attrs = f' [label="{label}"]'
        out.write(f'  "{node.instance_path}" -> "{child.instance_path}"{attrs};\n')
        _emit_edges(child, out)


def _format_port_connections(conns: tuple[PortConnection, ...]) -> str:
    """Render port connections as a compact ``.port(net), …`` label.

    Truncates after :data:`MAX_EDGE_LABEL_CONNECTIONS` entries with a
    ``…(+N more)`` tail so wide bus interfaces don't dominate the
    diagram.
    """
    parts: list[str] = []
    rendered = conns
    overflow = 0
    if len(conns) > MAX_EDGE_LABEL_CONNECTIONS:
        keep = MAX_EDGE_LABEL_CONNECTIONS - 1
        rendered = conns[:keep]
        overflow = len(conns) - keep
    for c in rendered:
        if c.port_name is None:
            # Positional connection — bare net expression.
            parts.append(_escape(c.net_expr_text))
        elif not c.net_expr_text:
            # Implicit `.port` shorthand — net expression is implied
            # to be a same-name net by the elaborator. Render
            # verbatim to keep source style.
            parts.append(f".{_escape(c.port_name)}")
        else:
            parts.append(f".{_escape(c.port_name)}({_escape(c.net_expr_text)})")
    if overflow:
        parts.append(f"…(+{overflow} more)")
    return ", ".join(parts)


def _label_for(node: HierNode, domain_map: DomainMap | None) -> str:
    inst_name = node.instance.name if node.instance is not None else node.module_name
    lines = [_escape(inst_name), _escape(node.module_name)]
    if node.instance is not None and node.instance.param_overrides:
        lines.append(_format_param_overrides(node.instance.param_overrides))
    if domain_map is not None:
        clock = domain_map.predominant_clock(node.instance_path)
        if clock is not None:
            lines.append(f"[{_escape(clock)}]")
    if node.is_blackbox:
        lines.append("(blackbox)")
    return r"\n".join(lines)


def _format_param_overrides(overrides: tuple[ParameterOverride, ...]) -> str:
    parts: list[str] = []
    for ov in overrides:
        if ov.param_name is None:
            # Positional override — bare value, positional index
            # implied by the order in the surrounding list.
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
