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
import re
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
    """Render ``node`` and its subtree as a Graphviz ``.dot`` digraph.

    The top module is rendered as a **frame** — a Graphviz cluster with
    the module name as its title, the module's input ports anchored on
    the left rank, output ports on the right rank, and the immediate
    children laid out inside. Treating top as a frame (not a node)
    matches the schematic-block mental model: the top *is* the
    external interface, not an instance of itself.

    Deeper nesting (children's children, etc.) still renders as the
    usual box-and-arrow tree.
    """
    active_map = domain_map if (domain_map and not domain_map.is_empty) else None
    out.write("digraph hierarchy {\n")
    # Left-to-right: input ports on the left rank, output ports on
    # the right rank.
    out.write('  rankdir="LR";\n')
    out.write("  compound=true;\n")
    # Tighter spacing — the frame + rank=same children pattern packs
    # vertically; reducing nodesep/ranksep keeps the diagram compact.
    out.write("  nodesep=0.18;\n")
    out.write("  ranksep=0.45;\n")
    out.write(
        f'  node [shape=box, style="rounded,filled", fillcolor="{_DEFAULT_FILL}"];\n'
    )
    out.write("\n")
    _emit_top_frame(node, out, active_map)
    if with_legend and active_map is not None:
        _emit_legend(active_map, out)
    out.write("}\n")


def _emit_top_frame(top: HierNode, out: IO[str], domain_map: DomainMap | None) -> None:
    """Emit the top module as a titled cluster with port-rank anchors."""
    title = _escape(top.module_name)
    clock = (
        domain_map.predominant_clock(top.instance_path)
        if domain_map is not None
        else None
    )
    if clock is not None:
        title = f"{title}  [{_escape(clock)}]"

    # Frame outline only — no fill — so the clock cue rides on the
    # border + per-child ``[clock]`` labels, not a wash of color that
    # competes with child node fills.
    outline = _palette_color(clock) if clock is not None else "#94a3b8"

    out.write("  subgraph cluster_top {\n")
    out.write(f'    label="{title}";\n')
    out.write('    labelloc="t";\n')
    out.write('    style="rounded";\n')
    out.write(f'    color="{outline}";\n')
    out.write("    penwidth=2;\n")

    _emit_port_anchors(top, out)

    # Direct children: let dot pick the rank for each — pinning them
    # all to ``rank=same`` lined them up neatly but made the diagram
    # impossible to read once children have grandchildren that fight
    # the constraint. Subtree depth dictates layout.
    for child in top.children:
        _emit_node(child, out, domain_map)
    for child in top.children:
        _emit_edges(child, out, domain_map)

    # Port → child signal-flow edges: when a child's port connection
    # has a bare-identifier net that matches a top input/output port
    # name, draw a thin clock-keyed edge between the port anchor and
    # the child. Clock and reset ports are skipped (they fan out to
    # nearly every flop and bury the data flow).
    _emit_port_signal_edges(top, out, domain_map)

    # CDC arrow: dashed-red overlay edge from input port anchor to
    # the child carrying an async crossing. Drawn AFTER the
    # signal-flow edges so the red arrow paints on top of the gray
    # one when both terminate at the same port + child pair.
    _emit_top_cdc_arrows(top, out, domain_map)

    out.write("  }\n")


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Clock/reset-like port names appearing as an underscore-delimited
# token. Used as a fallback when the domain map is absent
# (``clocks[].ports`` is the authoritative source when present).
# Patterns catch: clk, clk_a, a_clk, aclk style is too risky to
# strip (would false-positive on legit names containing "clk"); we
# require the token form. Same shape for rst/reset.
_CLOCK_NAME_RE = re.compile(r"(?i)(?:^|_)(clk|clock)(?:$|_)")
_RESET_NAME_RE = re.compile(r"(?i)(?:^|_)(rst|reset)(?:$|_)")
_NEUTRAL_EDGE_COLOR = "#cbd5e1"  # slate-300, falls back when no clock typing


def _emit_port_signal_edges(
    top: HierNode, out: IO[str], domain_map: DomainMap | None
) -> None:
    """Connect input/output port anchors to children by signal flow.

    For each direct child of the top: for each ``port_connection``
    whose ``net_expr_text`` is a bare identifier matching a top port
    name, emit a thin edge in the direction the port faces
    (input→child, child→output). Complex net expressions
    (concatenations, slices, expressions) are silently skipped — net
    tracing isn't in the renderer's scope.

    **Skipped**: clock-source ports (named in ``domain_map.clocks[].
    ports``) and reset-looking ports (``rst`` / ``reset`` token in the
    name). Wiring every flop's clock and reset back to the top input
    anchors produces a fan-out spiderweb that hides the actual data
    flow.

    **Color**: when a domain map is present, edges are colored by
    the clock domain the signal lives in:
      * port_domains[] lookup for ports typed via
        ``set_input_delay -clock`` / ``set_output_delay -clock``
      * fall back to the destination/source child's predominant
        clock
      * fall back to a neutral slate when no clock is determinable.
    """
    if top.module is None or not top.module.ports:
        return
    inputs = {p.name for p in top.module.ports if p.direction == "input"}
    outputs = {p.name for p in top.module.ports if p.direction in ("output", "inout")}
    clock_ports: set[str] = set()
    port_clock: dict[str, str] = {}
    if domain_map is not None:
        for clk in domain_map.clocks:
            clock_ports.update(clk.ports)
        for pd in domain_map.port_domains:
            port_clock[pd.port] = pd.clock

    for child in top.children:
        if child.instance is None:
            continue
        seen_in: set[str] = set()
        seen_out: set[str] = set()
        for conn in child.instance.port_connections:
            net = conn.net_expr_text.strip() if conn.net_expr_text else ""
            if not _IDENTIFIER_RE.match(net):
                continue
            if (
                net in clock_ports
                or _CLOCK_NAME_RE.search(net)
                or _RESET_NAME_RE.search(net)
            ):
                continue
            if net in inputs and net not in seen_in:
                seen_in.add(net)
                color = _signal_edge_color(net, child, domain_map, port_clock)
                out.write(
                    f'    "_in_{net}" -> "{child.instance_path}" '
                    f'[color="{color}", penwidth=1.2, arrowsize=0.6];\n'
                )
            if net in outputs and net not in seen_out:
                seen_out.add(net)
                color = _signal_edge_color(net, child, domain_map, port_clock)
                out.write(
                    f'    "{child.instance_path}" -> "_out_{net}" '
                    f'[color="{color}", penwidth=1.2, arrowsize=0.6];\n'
                )


def _signal_edge_color(
    port_name: str,
    child: HierNode,
    domain_map: DomainMap | None,
    port_clock: dict[str, str],
) -> str:
    """Pick the clock-keyed palette color for a signal-flow edge.

    Resolution order: explicit ``port_domains[]`` typing first
    (authoritative — set by ``set_input_delay -clock`` /
    ``set_output_delay -clock``), then the child's predominant
    clock as a structural fallback, then a neutral slate-300.
    """
    if domain_map is None:
        return _NEUTRAL_EDGE_COLOR
    clk = port_clock.get(port_name)
    if clk is None:
        clk = domain_map.predominant_clock(child.instance_path)
    if clk is None:
        return _NEUTRAL_EDGE_COLOR
    return _palette_color(clk)


def _emit_port_anchors(top: HierNode, out: IO[str]) -> None:
    """Emit input/output port markers in source/sink rank groups.

    Input ports anchor to ``rank=source`` (left edge under LR), output
    and inout ports to ``rank=sink`` (right edge). Anchors are
    ``plaintext`` shape so they read as labels, not boxes.

    Alignment within the label uses Graphviz line-terminators:
    ``\\r`` right-aligns input labels (arrows form a column on the
    right, closest to the children); ``\\l`` left-aligns output labels
    (arrows form a column on the left, closest to the children).
    """
    if top.module is None or not top.module.ports:
        return
    inputs = [p for p in top.module.ports if p.direction == "input"]
    outputs = [p for p in top.module.ports if p.direction in ("output", "inout")]

    if inputs:
        out.write("    { rank=source;\n")
        for p in inputs:
            label = _escape(p.name)
            out.write(
                f'      "_in_{p.name}" '
                f'[shape=plaintext, label="{label} ▶\\r", fontsize=9];\n'
            )
        out.write("    }\n")
    if outputs:
        out.write("    { rank=sink;\n")
        for p in outputs:
            label = _escape(p.name)
            out.write(
                f'      "_out_{p.name}" '
                f'[shape=plaintext, label="▶ {label}\\l", fontsize=9];\n'
            )
        out.write("    }\n")


def _emit_top_cdc_arrows(
    top: HierNode, out: IO[str], domain_map: DomainMap | None
) -> None:
    """Emit a dashed-red arrow from an input-port anchor to a child with
    a CDC crossing in. The src_clock must be the name of one of the
    top's input ports; otherwise the crossing is left silently visible
    only through the per-child ``[clock]`` label.
    """
    if domain_map is None:
        return
    if top.module is None:
        return
    input_names = {p.name for p in top.module.ports if p.direction == "input"}
    for child in top.children:
        # ``crossings_into`` is filtered to ``async_per_sdc=True`` by
        # default — the SDC-confirmed true-CDC subset that the tree /
        # mermaid renderers also surface.
        seen: set[tuple[str, str]] = set()
        for c in domain_map.crossings_into(child.instance_path):
            if c.src_clock not in input_names:
                continue
            key = (c.src_clock, c.dst_clock)
            if key in seen:
                continue
            seen.add(key)
            label = f"⚠CDC: {_escape(c.src_clock)}→{_escape(c.dst_clock)}"
            out.write(
                f'    "_in_{c.src_clock}" -> "{child.instance_path}" '
                f'[label="{label}", color="#dc2626", '
                f'style="dashed", fontcolor="#dc2626"];\n'
            )


def _emit_node(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    *,
    cdc_on_node: bool = False,
) -> None:
    """Emit a node line.

    ``cdc_on_node`` is set for direct children of the top frame: those
    children would normally carry their CDC marker on the incoming
    edge from the top, but in cluster mode that edge doesn't exist —
    so the marker collapses onto the node itself (label suffix +
    red border).
    """
    label = _label_for(node, domain_map)
    attrs = [f'label="{label}"']
    fill = _fill_for(node, domain_map)
    if node.is_blackbox:
        attrs.append('style="rounded,filled,dashed"')
        attrs.append(f'fillcolor="{fill}"')
    elif fill != _DEFAULT_FILL:
        attrs.append(f'fillcolor="{fill}"')
    if cdc_on_node:
        cdc_summary = _format_cdc_summary(node, domain_map)
        if cdc_summary:
            # Append CDC summary as an extra label line, red the
            # border + text so it reads at a glance.
            attrs[0] = f'label="{label}\\n{cdc_summary}"'
            attrs.append('color="#dc2626"')
            attrs.append("penwidth=2")
            attrs.append('fontcolor="#dc2626"')
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


def _emit_edges(
    node: HierNode, out: IO[str], domain_map: DomainMap | None = None
) -> None:
    for child in node.children:
        attr_parts: list[str] = []
        port_label = (
            _format_port_connections(child.instance.port_connections)
            if child.instance is not None and child.instance.port_connections
            else ""
        )
        cdc_summary = _format_cdc_summary(child, domain_map)
        label = port_label
        if cdc_summary:
            label = f"{cdc_summary}\\n{port_label}" if port_label else cdc_summary
        if label:
            attr_parts.append(f'label="{label}"')
        if cdc_summary:
            # Distinct edge style for CDC: dashed red so the
            # hazard reads instantly even on a busy diagram.
            attr_parts.append('color="#dc2626"')
            attr_parts.append('style="dashed"')
            attr_parts.append('fontcolor="#dc2626"')
        attrs = f" [{', '.join(attr_parts)}]" if attr_parts else ""
        out.write(f'  "{node.instance_path}" -> "{child.instance_path}"{attrs};\n')
        _emit_edges(child, out, domain_map)


def _format_cdc_summary(child: HierNode, domain_map: DomainMap | None) -> str:
    """Build the ``⚠CDC: clk_a→clk_b`` label fragment for a CDC edge.

    Returns an empty string when no async crossing terminates at
    ``child``. Multiple crossings from distinct source clocks
    collapse to a single deterministic list — matches the tree
    renderer's marker shape so reviewers see the same summary across
    formats.
    """
    if domain_map is None or domain_map.is_empty:
        return ""
    crossings = domain_map.crossings_into(child.instance_path)
    if not crossings:
        return ""
    sources_by_dst: dict[str, set[str]] = {}
    for c in crossings:
        sources_by_dst.setdefault(c.dst_clock, set()).add(c.src_clock)
    parts: list[str] = []
    for dst in sorted(sources_by_dst):
        for src in sorted(sources_by_dst[dst]):
            parts.append(f"{src}→{dst}")
    return "⚠CDC: " + ", ".join(parts)


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
    # ``\l`` is Graphviz's left-aligned newline. One port-connection per
    # line keeps the label tall instead of wide so Graphviz's layout
    # engine has room to place adjacent edges without overlap. Trailing
    # ``\l`` anchors the final line to the left margin too.
    return r"\l".join(parts) + (r"\l" if parts else "")


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
    """Render parameter overrides as a multi-line ``#( … )`` block.

    One param per line, two-space indented, ``\\l`` left-aligned —
    same line-break convention as edge port-connection labels. Wide
    parameter lists no longer stretch the node horizontally.
    """
    parts: list[str] = []
    for ov in overrides:
        if ov.param_name is None:
            # Positional override — bare value, positional index
            # implied by the order in the surrounding list.
            parts.append(_escape(ov.value_text))
        else:
            parts.append(f".{_escape(ov.param_name)}({_escape(ov.value_text)})")
    if not parts:
        return "#()"
    body = r"\l  ".join(parts)
    return r"#(\l  " + body + r"\l)"


def _escape(s: str) -> str:
    """Escape a string for use inside a Graphviz quoted label.

    Graphviz labels need backslashes and double-quotes escaped; we
    also strip embedded newlines and collapse runs of whitespace so a
    multi-line type slice (rare but possible from the source-slice
    extractor) renders on one line within its label segment.
    """
    cleaned = " ".join(s.split())
    return cleaned.replace("\\", "\\\\").replace('"', '\\"')
