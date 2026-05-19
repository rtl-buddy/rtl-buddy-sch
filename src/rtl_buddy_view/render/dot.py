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
    # Fixed-width font on every label so space-padding visually aligns
    # parentheses in parameter and port-connection lists.
    out.write('  fontname="Menlo,Courier,monospace";\n')
    out.write(
        f'  node [shape=box, style="rounded,filled", fillcolor="{_DEFAULT_FILL}",'
        ' fontname="Menlo,Courier,monospace"];\n'
    )
    out.write('  edge [fontname="Menlo,Courier,monospace"];\n')
    out.write("\n")
    _emit_top_frame(node, out, active_map)
    if with_legend and active_map is not None:
        _emit_legend(active_map, out)
    out.write("}\n")


def _emit_top_frame(top: HierNode, out: IO[str], domain_map: DomainMap | None) -> None:
    """Emit the top module as a titled cluster with port-rank anchors."""
    title = _escape(top.module_name)
    clocks_in_subtree = (
        _clocks_under(top.instance_path, domain_map) if domain_map is not None else ()
    )
    if clocks_in_subtree:
        title = f"{title}  [{', '.join(_escape(c) for c in clocks_in_subtree)}]"

    # Frame outline only — no fill — so the clock cue rides on the
    # border + per-child ``[clock]`` labels, not a wash of color that
    # competes with child node fills. Single-clock subtrees get a
    # clock-keyed outline; multi-clock or untyped subtrees get a
    # neutral slate so the per-child fills carry the domain detail.
    if len(clocks_in_subtree) == 1:
        outline = _palette_color(clocks_in_subtree[0])
    else:
        outline = "#94a3b8"

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

    # Restore the directional CDC arrow: dashed-red edge from the
    # corresponding input-port anchor to the child carrying the
    # crossing. When the src_clock isn't an input port, we silently
    # fall back to leaving the marker on the per-child node label —
    # ``predominant_clock`` already labels it, and the renderer
    # surfaces ``crossings_in`` through the JSON contract regardless.
    _emit_top_cdc_arrows(top, out, domain_map)

    out.write("  }\n")


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
    # Multi-direction CDC: distinct (src, dst) crossing pairs > 1.
    # These need a 2-column grid (one row per direction), which dot's
    # ``striped`` style can't do — fall through to an HTML-label
    # rendering path.
    pairs = (
        _crossing_pairs_into(node.instance_path, domain_map)
        if domain_map is not None and not node.is_blackbox
        else ()
    )
    if len(pairs) > 1:
        _emit_html_grid_node(node, out, domain_map, pairs, cdc_on_node=cdc_on_node)
        for child in node.children:
            _emit_node(child, out, domain_map)
        return

    label = _label_for(node, domain_map)
    attrs = [f'label="{label}"']
    fill, striped = _fill_for(node, domain_map)
    if node.is_blackbox:
        # Blackbox dashed + filled; striped not combined with dashed
        # (Graphviz can't render both). Falls back to solid fill.
        attrs.append('style="rounded,filled,dashed"')
        attrs.append(f'fillcolor="{fill}"')
    elif striped:
        # Two-tone src→dst gradient: left half source-clock color,
        # right half destination-clock color. ``box`` shape under
        # ``striped`` style emits vertical bands left-to-right.
        attrs.append('style="rounded,striped"')
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


def _emit_html_grid_node(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    pairs: tuple[tuple[str, str], ...],
    *,
    cdc_on_node: bool,
) -> None:
    """Emit a node as an HTML-label table — header text + one row per crossing.

    Used for nodes that have multiple distinct ``(src, dst)`` crossing
    pairs (typically gray-code CDC FIFOs that cross both ways). Dot's
    ``striped`` style only emits vertical bands; HTML labels let us
    emit a 2-column grid where each row shows one direction.

    The header cell carries the same text content as a regular node
    label (instance, module, parameter block, clock summary, blackbox
    marker), using ``<BR ALIGN="LEFT"/>`` for left-aligned line
    breaks so it visually matches the rest of the diagram.
    """
    text_lines = _label_text_lines(node, domain_map)
    header = '<BR ALIGN="LEFT"/>'.join(_html_escape(ln) for ln in text_lines)
    # Trailing <BR/> anchors the last line to the left, matching the
    # ``\l`` behavior in plain labels.
    header += '<BR ALIGN="LEFT"/>'

    rows: list[str] = [
        f'<TR><TD COLSPAN="2" BORDER="0" ALIGN="LEFT">'
        f'<FONT FACE="Menlo,Courier,monospace">{header}</FONT></TD></TR>'
    ]
    for src, dst in pairs:
        rows.append(
            f"<TR>"
            f'<TD BGCOLOR="{_palette_color(src)}" PORT="src_{src}" WIDTH="60">'
            f'<FONT POINT-SIZE="10">{_html_escape(src)}</FONT></TD>'
            f'<TD BGCOLOR="{_palette_color(dst)}" PORT="dst_{dst}" WIDTH="60">'
            f'<FONT POINT-SIZE="10">{_html_escape(dst)}</FONT></TD>'
            f"</TR>"
        )
    table = (
        '<<TABLE BORDER="1" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">'
        + "".join(rows)
        + "</TABLE>>"
    )

    attrs = [f"label={table}", "shape=plaintext"]
    if cdc_on_node:
        attrs.append('color="#dc2626"')
        attrs.append("penwidth=2")
    out.write(f'  "{node.instance_path}" [{", ".join(attrs)}];\n')


def _label_text_lines(node: HierNode, domain_map: DomainMap | None) -> list[str]:
    """Return the per-line text content that ``_label_for`` would render,
    minus the ``\\l`` joining — useful for HTML-label emission.
    """
    inst_name = node.instance.name if node.instance is not None else node.module_name
    lines = [inst_name, node.module_name]
    if node.instance is not None and node.instance.param_overrides:
        # Param block already carries ``\l`` separators; flatten to a
        # list of bare lines for HTML conversion.
        block = _format_param_overrides(node.instance.param_overrides)
        # Re-split on the ``\l`` markers; each is a literal two-char
        # ``\\l`` in the formatter output.
        lines.extend(s for s in block.split(r"\l") if s)
    if domain_map is not None:
        clocks = _clocks_under(node.instance_path, domain_map)
        if clocks:
            lines.append(f"[{', '.join(clocks)}]")
    if node.is_blackbox:
        lines.append("(blackbox)")
    return lines


def _html_escape(s: str) -> str:
    """Minimal HTML escape for dot HTML labels.

    Dot HTML labels are XML-like; we need ``&``, ``<``, ``>``, and
    quotes encoded. Whitespace is preserved (padding spaces in
    parameter alignment depend on it under the monospace font).
    """
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fill_for(node: HierNode, domain_map: DomainMap | None) -> tuple[str, bool]:
    """Pick the node's fill spec.

    Returns ``(fillcolor_value, is_striped)``. When ``is_striped`` is
    true, the caller emits ``style="rounded,striped"`` so the colon-
    separated palette pair renders as a left/right two-tone fill.

    Resolution:

    * No domain map → blackbox-yellow / default gray, solid.
    * Subtree in a single clock domain → that clock's palette swatch,
      solid.
    * Subtree spans exactly two clock domains AND every crossing
      that terminates in this subtree uses one of those clocks as
      ``src_clock`` and the other as ``dst_clock`` → two-tone:
      ``<src palette>:<dst palette>`` (left half = source, right
      half = destination). Makes CDC synchronizers / FIFOs read at a
      glance as "data flows from this side to that side."
    * Anything else (≥3 clocks, or 2 clocks with no clear direction)
      → neutral default fill, solid.
    """
    if domain_map is None:
        return (
            _BLACKBOX_FILL if node.is_blackbox else _DEFAULT_FILL,
            False,
        )
    # Two-tone case takes priority over single-clock: a one-stage
    # sync flop has its flops in the destination clock but receives
    # data from a source clock — the crossing carries the directional
    # info that the per-flop count doesn't.
    direction = _crossing_direction(node.instance_path, domain_map)
    if direction is not None:
        src, dst = direction
        return f"{_palette_color(src)}:{_palette_color(dst)}", True
    clocks = _clocks_under(node.instance_path, domain_map)
    if len(clocks) == 1:
        return _palette_color(clocks[0]), False
    return (
        _BLACKBOX_FILL if node.is_blackbox else _DEFAULT_FILL,
        False,
    )


def _crossing_pairs_into(
    instance_path: str, domain_map: DomainMap
) -> tuple[tuple[str, str], ...]:
    """All distinct ``(src_clock, dst_clock)`` pairs from async crossings
    terminating in the subtree rooted at ``instance_path``.

    Sorted for deterministic output. Crossings with an ``<unconstrained>``
    source are excluded — no real clock to anchor a direction on.
    """
    prefix = instance_path + "."
    pairs: set[tuple[str, str]] = set()
    for c in domain_map.crossings:
        if not c.async_per_sdc:
            continue
        if c.src_clock.startswith("<") and c.src_clock.endswith(">"):
            continue
        dst_owner = c.dst_source_instance_path or c.dst_flop
        if dst_owner != instance_path and not dst_owner.startswith(prefix):
            continue
        pairs.add((c.src_clock, c.dst_clock))
    return tuple(sorted(pairs))


def _crossing_direction(
    instance_path: str,
    domain_map: DomainMap,
) -> tuple[str, str] | None:
    """Return ``(src_clock, dst_clock)`` when crossings into ``instance_path``
    define a single unambiguous direction.

    Returns ``None`` for bidirectional / no-crossing / multiple-direction
    cases. Multi-direction nodes get the HTML-label grid treatment in
    :func:`_emit_html_grid_node` instead of a striped two-tone fill.
    """
    pairs = _crossing_pairs_into(instance_path, domain_map)
    if len(pairs) != 1:
        return None
    return pairs[0]


def _palette_color(clock_name: str) -> str:
    """Deterministic clock→palette mapping via stable digest.

    Using a hash (not Python's ``hash()``) keeps colors stable across
    Python processes and PYTHONHASHSEED values — important when the
    output is checked into golden files or feeding visual diff tools.
    """
    digest = hashlib.sha256(clock_name.encode("utf-8")).digest()
    return _CLOCK_PALETTE[digest[0] % len(_CLOCK_PALETTE)]


def _clocks_under(instance_path: str, domain_map: DomainMap) -> tuple[str, ...]:
    """All distinct clock domains present under ``instance_path``, sorted.

    Used for the top frame's title — ``predominant_clock`` would
    silently flatten a multi-clock module to its majority domain,
    which is misleading for any design with crossings (which is most
    of what you'd want a CDC-overlay rendering for).
    """
    prefix = instance_path + "."
    found: set[str] = set()
    for flop in domain_map.flop_domains:
        if flop.clock is None:
            continue
        owner = flop.source_instance_path or flop.instance_path
        if owner == instance_path or owner.startswith(prefix):
            found.add(flop.clock)
    return tuple(sorted(found))


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

    # Pad ``.port_name`` to the max width in this list so the ``(``
    # parens form a single column under the fixed-width font. Padding
    # has to land *after* ``_escape`` since the latter collapses
    # whitespace runs.
    escaped_names = {
        c.port_name: _escape(c.port_name) for c in rendered if c.port_name is not None
    }
    pad = max((len(s) for s in escaped_names.values()), default=0)

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
            padded = escaped_names[c.port_name].ljust(pad)
            parts.append(f".{padded}({_escape(c.net_expr_text)})")
    if overflow:
        parts.append(f"…(+{overflow} more)")
    # ``\l`` is Graphviz's left-aligned newline. One port-connection per
    # line keeps the label tall instead of wide so Graphviz's layout
    # engine has room to place adjacent edges without overlap. Trailing
    # ``\l`` anchors the final line to the left margin too.
    return r"\l".join(parts) + (r"\l" if parts else "")


def _label_for(node: HierNode, domain_map: DomainMap | None) -> str:
    """Build a left-aligned, fixed-width node label.

    Lines stack via ``\\l`` (Graphviz left-aligned newline) so the
    instance name, module name, parameter block, clock tag, and any
    blackbox marker all flush against the left edge of the box —
    matches the param-block alignment and reads naturally under the
    monospace font.

    The clock tag enumerates every clock present in the subtree
    (e.g. ``[a_clk, s_clk, vu_clk]``), not just the predominant one
    — a single-clock label on a multi-clock module hides the
    crossings that the diagram is meant to surface.
    """
    inst_name = node.instance.name if node.instance is not None else node.module_name
    lines = [_escape(inst_name), _escape(node.module_name)]
    if node.instance is not None and node.instance.param_overrides:
        lines.append(_format_param_overrides(node.instance.param_overrides))
    if domain_map is not None:
        clocks = _clocks_under(node.instance_path, domain_map)
        if clocks:
            lines.append(f"[{', '.join(_escape(c) for c in clocks)}]")
    if node.is_blackbox:
        lines.append("(blackbox)")
    # Trailing ``\l`` anchors the final line to the left margin.
    return r"\l".join(lines) + r"\l"


def _format_param_overrides(overrides: tuple[ParameterOverride, ...]) -> str:
    """Render parameter overrides as a multi-line ``#( … )`` block.

    One param per line, two-space indented, ``\\l`` left-aligned —
    same line-break convention as edge port-connection labels. Wide
    parameter lists no longer stretch the node horizontally.
    """
    # Pad ``.PARAM`` to the max name width so the ``(`` parens line
    # up vertically under the fixed-width font. Padding has to land
    # *after* ``_escape`` since the latter collapses whitespace runs.
    escaped_names = {
        ov.param_name: _escape(ov.param_name)
        for ov in overrides
        if ov.param_name is not None
    }
    pad = max((len(s) for s in escaped_names.values()), default=0)

    parts: list[str] = []
    for ov in overrides:
        if ov.param_name is None:
            # Positional override — bare value, positional index
            # implied by the order in the surrounding list.
            parts.append(_escape(ov.value_text))
        else:
            padded = escaped_names[ov.param_name].ljust(pad)
            parts.append(f".{padded}({_escape(ov.value_text)})")
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
