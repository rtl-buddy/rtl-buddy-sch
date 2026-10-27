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

import dataclasses
import io
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import IO

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.axi_perf_annotations import AxiPerfMap
from rtl_buddy_view.clock_colors import (
    CLOCK_PALETTE,
    assign_clock_colors,
    domain_map_clocks,
    hash_color,
)
from rtl_buddy_view.connectivity import (
    _CLOCK_NAME_RE,
    _RESET_NAME_RE,
    Endpoint,
    NetEdge,
    _fold_bundle_returns,
    boundary_directions,
    logic_nets,
    module_param_defaults,
    port_width,
    scope_connectivity,
)
from rtl_buddy_view.extractor import (
    ModuleTable,
    ParameterOverride,
    Port,
    PortConnection,
    flatten_interface_ports,
)
from rtl_buddy_view.graph import HierNode, without_interface_instances
from rtl_buddy_view.hints import HintMap
from rtl_buddy_view.reset_annotations import ResetDomainMap

MAX_EDGE_LABEL_CONNECTIONS = 6
"""Cap on port connections rendered inline on an edge label.

Beyond this we render the first ``MAX_EDGE_LABEL_CONNECTIONS - 1``
entries followed by ``…(+N more)`` so the diagram stays readable on
wide bus interfaces. The data model itself is untouched."""

# A small palette of distinct pastel fills tuned for legibility on
# both light and dark backgrounds, now owned by
# :mod:`rtl_buddy_view.clock_colors`. A figure deals the swatches out
# to its clocks (chosen colours first, the rest in sorted order) so no
# two domains of one figure collide; the name hash is the fallback
# for a figure with more clocks than swatches (rtl-buddy-sch#196).
_CLOCK_PALETTE: tuple[str, ...] = CLOCK_PALETTE

#: The clock → colour assignment of the figure being rendered, set by
#: :func:`figure_clock_colors` for the duration of one ``render`` call.
#: Render-scoped context rather than a parameter on every helper: a
#: clock is coloured from a dozen places (fills, cluster outlines, the
#: two-tone crossings, grid cells, signal edges, the legend), and they
#: must all read the *same* assignment. A context variable is reset on
#: exit, so a nested render (``json_render`` embeds one) can't leak.
_FIGURE_COLORS: ContextVar[Mapping[str, str] | None] = ContextVar(
    "_FIGURE_COLORS", default=None
)


@contextmanager
def figure_clock_colors(
    domain_map: DomainMap | None, chosen: Mapping[str, str] | None = None
) -> Iterator[None]:
    """Fix one figure's clock colours for the duration of the block.

    Shared by the dot and mermaid renderers so both assign the same
    way: ``chosen`` (author-picked, see
    :func:`rtl_buddy_view.clock_colors.chosen_clock_colors`) first,
    then every other clock the domain map names, dealt the swatches in
    sorted order.
    """
    clocks = domain_map_clocks(domain_map) if domain_map is not None else ()
    token = _FIGURE_COLORS.set(assign_clock_colors(clocks, chosen))
    try:
        yield
    finally:
        _FIGURE_COLORS.reset(token)


_DEFAULT_FILL = "#f5f5f5"
_BLACKBOX_FILL = "#fff8e0"

#: RDC overlay accent color. Distinct from the CDC red (``#dc2626``)
#: so a flop that has both crossings can still be read at a glance
#: when CDC and RDC markers stack on the same node/edge. Tailwind
#: ``orange-600`` — sits between yellow and red without colliding
#: with either the clock-palette pastels or the blackbox-yellow fill.
_RDC_COLOR = "#ea580c"

#: Reset-synchronizer outline color. Tailwind ``teal-600`` — reads
#: as a "vetted" / safe marker against the warning-coloured RDC
#: edges; deliberately not green (which would clash with the
#: clock-palette ``#dcfce7`` swatch on light fills).
_RSTSYNC_OUTLINE = "#0d9488"


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
    reset_map: ResetDomainMap | None = None,
    axi_perf_map: AxiPerfMap | None = None,
    with_legend: bool = False,
    as_cluster_tree: bool = False,
    block_diagram: bool = False,
    module_table: ModuleTable | None = None,
    hints: HintMap | None = None,
    clock_colors: Mapping[str, str] | None = None,
    prune_flags: bool = False,
) -> None:
    """Render ``node`` and its subtree as a Graphviz ``.dot`` digraph.

    The top module is rendered as a **frame** — a Graphviz cluster with
    the module name as its title, the module's input ports anchored on
    the left rank, output ports on the right rank, and the immediate
    children laid out inside. Treating top as a frame (not a node)
    matches the schematic-block mental model: the top *is* the
    external interface, not an instance of itself.

    ``as_cluster_tree`` extends the cluster treatment to *every*
    non-leaf instance: each sub-block becomes its own bounding box
    containing its sub-cells, so containment reads as containment.
    Parent → child edges drop in this mode (the wrapper *is* the
    relationship); CDC / RDC adornments migrate to the destination
    cluster's border. Default is ``False`` so the standalone
    ``--format dot`` output keeps the established box-and-arrow
    shape; the JSON-embedded layout (consumed by the SPA) opts in.

    With ``as_cluster_tree=False``, deeper nesting (children's
    children, etc.) renders as the usual box-and-arrow tree.

    ``block_diagram`` switches the graph from *hierarchy* to
    *dataflow* — the documentation-diagram shape. It implies
    ``as_cluster_tree`` (nesting reads as containment), drops the
    parent→child port-map edges entirely, and instead draws
    sibling-to-sibling net edges computed by
    :func:`rtl_buddy_view.connectivity.scope_connectivity` at *every*
    scope that resolved to a module. Interface ports render as one
    bundle anchor rather than one anchor per flattened signal — an
    APB port is a single block-diagram terminal, not eleven wires.
    Interface *instances* are not drawn at all: on a dataflow diagram
    an interface is the wire between the blocks bound to it, and the
    edges through it are oriented by those blocks' modports
    (rtl-buddy-sch#194). Requires ``module_table`` (pin directions come from the child
    modules); without one the dataflow edges are skipped and the
    output degrades to a plain cluster tree. CDC / RDC overlay arrows
    keep working.

    ``hints`` carries the resolved ``rbsch`` hint map (epic #159).
    Only its *net* vocabulary is consumed here — classification
    reaches the connectivity analyzer so hinted clocks drop out of
    (and hinted data survives into) the dataflow, and ``main`` /
    ``side`` emphasis styles the resulting edges. The box vocabulary
    was already applied to the hierarchy before rendering. ``None``
    is byte-identical to before the parameter existed.

    When ``reset_map`` is supplied (Phase 3), the renderer also emits:

    - A ``[resets: rst_n↓, …]`` line on each subtree that contains
      reset-bearing flops, alongside the existing clock summary.
    - A teal-outlined node for instances flagged as reset
      synchronizers (``reset_map.synchronizer_paths()``).
    - A dashed-orange RDC edge / arrow for crossings — distinct from
      the dashed-red CDC marker so a node that is both a CDC and RDC
      destination reads at a glance.

    ``clock_colors`` maps clock name → colour for the clocks the author
    chose a colour for (rtl-buddy-sch#196); every other clock in the
    domain map is dealt a distinct swatch in sorted order
    (:func:`figure_clock_colors`). Only consulted with a domain map.

    ``prune_flags`` (block diagram only, rtl-buddy-sch#196) drops every
    root boundary flag that no edge in the figure lands on — clock and
    reset ports whose edges the dataflow classifier dropped are the
    usual case. A flag any edge or overlay arrow names is always kept,
    and so is one whose port the top's own logic reads or writes
    (:func:`~rtl_buddy_view.connectivity.logic_nets`, rtl-buddy-sch#210).
    """
    active_map = domain_map if (domain_map and not domain_map.is_empty) else None
    with figure_clock_colors(active_map, clock_colors):
        _render_figure(
            node,
            out,
            active_map,
            reset_map,
            axi_perf_map=axi_perf_map,
            with_legend=with_legend,
            as_cluster_tree=as_cluster_tree,
            block_diagram=block_diagram,
            module_table=module_table,
            hints=hints,
            prune_flags=prune_flags,
        )


def _render_figure(
    node: HierNode,
    out: IO[str],
    active_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    *,
    axi_perf_map: AxiPerfMap | None,
    with_legend: bool,
    as_cluster_tree: bool,
    block_diagram: bool,
    module_table: ModuleTable | None,
    hints: HintMap | None,
    prune_flags: bool,
) -> None:
    """:func:`render`'s body, inside the figure's clock-colour scope."""
    active_reset_map = reset_map if reset_map is not None else None
    if block_diagram and module_table is not None:
        # An interface instance is the wire between the blocks bound
        # to it, not a box (rtl-buddy-sch#194).
        node = without_interface_instances(node, module_table)
    # DOT's edges are parent→child by hierarchy; AXI bundles
    # connect siblings (CPU↔DRAM at the same level) that DOT
    # doesn't emit as explicit edges. The design pivoted away from
    # per-edge styling (#69) — AXI perf is surfaced in the viewer's
    # AXI tab and in json_render, not on the DOT graph — so DOT
    # intentionally leaves edges unstyled. The parameter is accepted
    # only to keep the CLI plumbing uniform with json_render.
    del axi_perf_map  # intentionally unused — see comment above
    out.write("digraph hierarchy {\n")
    # Left-to-right: input ports on the left rank, output ports on
    # the right rank.
    out.write('  rankdir="LR";\n')
    out.write("  compound=true;\n")
    # Orthogonal routing — right-angle edges read as a schematic
    # rather than a flow diagram. The trade-off is that dot's ortho
    # router can't share start/end points among multiple edges
    # touching the same node, so dense fan-in/fan-out can look more
    # crowded than with curved splines. The schematic aesthetic
    # wins for RTL hierarchy.
    out.write('  splines="ortho";\n')
    # ``ranksep`` controls the spacing between successive ranks
    # (columns under ``rankdir=LR``). Bumping it to 1.2 puts a
    # clear gap between input-port anchors, the central cluster
    # column, and the output-port anchors — schematic skim
    # convention. ``nodesep`` stays tight so per-cluster contents
    # don't inflate vertically.
    out.write("  nodesep=0.18;\n")
    out.write("  ranksep=1.2;\n")
    # Fixed-width font on every label so space-padding visually aligns
    # parentheses in parameter and port-connection lists.
    out.write('  fontname="Courier,monospace";\n')
    # ``margin=0.4,0.06`` (inches) reserves a horizontal cushion
    # inside each node polygon so labels can't reach the right
    # edge. viz.js's WASM bundle uses Liberation-style metrics for
    # layout but the browser renders the named ``Courier`` family
    # noticeably wider, so labels overflow Graphviz's reserved
    # space — without the cushion, long module-type names (e.g.
    # ``ip_dtnpu_clk_buf``) bleed past the polygon. Graphviz's
    # margin attribute is symmetric (horizontal applies to both
    # left and right); we trade some wasted left padding for the
    # right-side headroom labels actually need. The vertical
    # cushion stays small so dense hierarchies don't inflate.
    out.write(
        f'  node [shape=box, style="rounded,filled", fillcolor="{_DEFAULT_FILL}",'
        ' fontname="Courier,monospace", margin="0.4,0.06"];\n'
    )
    out.write('  edge [fontname="Courier,monospace"];\n')
    out.write("\n")
    _emit_top_frame(
        node,
        out,
        active_map,
        active_reset_map,
        as_cluster_tree=as_cluster_tree or block_diagram,
        block_diagram=block_diagram,
        module_table=module_table,
        hints=hints,
        prune_flags=prune_flags,
    )
    if with_legend and active_map is not None:
        _emit_legend(active_map, out)
    out.write("}\n")


def _emit_top_frame(
    top: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
    *,
    as_cluster_tree: bool = False,
    block_diagram: bool = False,
    module_table: ModuleTable | None = None,
    hints: HintMap | None = None,
    prune_flags: bool = False,
) -> None:
    """Emit the top module as a titled cluster with port-rank anchors.

    In block-diagram mode the root's boundary flags are
    :func:`_root_flags` — one per visible port, or one per root-port
    ``bundle=`` — and the edges are written to a buffer first, so
    ``prune_flags`` can keep exactly the flags they name. The buffer is
    written back at the same place in the output, so the bytes are
    unchanged when nothing is pruned.
    """
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
    # Extra interior margin so ``splines="ortho"`` has room to route
    # port → cluster edges WITHOUT bending past the cluster_top
    # boundary. Ortho routing is rigid — when a corridor is tight it
    # detours through the cluster's outer perimeter, visually
    # "escaping" the top frame. 20pt all sides is a good fit for
    # ip_demo_tiny_npu-class designs (deep cluster nesting + many
    # top-port edges); cheaper than relaxing nodesep/ranksep which
    # would inflate every cluster.
    out.write('    margin="20,20";\n')

    flags: tuple[_RootFlag, ...] = ()
    edge_text = ""
    if block_diagram:
        flags = _root_flags(top, module_table, hints)
        edge_buf = io.StringIO()
        _emit_connectivity_edges(top, edge_buf, module_table, hints, flags=flags)
        flag_ids = _flag_ids_by_port(flags, top)
        _emit_top_cdc_arrows(
            top,
            edge_buf,
            domain_map,
            as_cluster_tree=as_cluster_tree,
            flag_ids=flag_ids,
        )
        if reset_map is not None:
            _emit_top_rdc_arrows(
                top,
                edge_buf,
                reset_map,
                as_cluster_tree=as_cluster_tree,
                flag_ids=flag_ids,
            )
        edge_text = edge_buf.getvalue()
        if prune_flags:
            referenced = {
                end for pair in _EDGE_LINE_RE.findall(edge_text) for end in pair
            }
            # A port the body's own logic reads (or writes) is real
            # dataflow even when no edge lands on its flag
            # (rtl-buddy-sch#210) — keep it; clocks and resets are left
            # out of ``logic_nets`` and still go.
            in_logic = (
                logic_nets(
                    top.module,
                    hints.nets_for_module(top.module_name)
                    if hints is not None
                    else None,
                )
                if top.module is not None
                else frozenset()
            )
            flags = tuple(
                f
                for f in flags
                if f.node_id in referenced or in_logic.intersection(f.members)
            )
        _emit_root_flags(flags, out)
    else:
        _emit_port_anchors(top, out, module_table=module_table)

    # The grid packer (#138) only has a job in cluster-tree mode: the
    # box-and-arrow tree has parent→child edges to rank by, and the
    # block diagram has sibling dataflow — it is only the cluster tree
    # whose siblings carry no edge at all and collapse into one column.
    pack_frame = _PackFrame()
    pack_plan: dict[str, _ScopePack] | None = None
    if as_cluster_tree and not block_diagram:
        # The port anchors flank the child grid: one rank of them on
        # the left, one on the right. Their combined width and their
        # (taller of the two) height are what the packer sizes against.
        inputs, outputs = _split_ports(top, module_table=module_table)
        in_w, in_h = _port_anchor_extent([p.name for p in inputs])
        out_w, out_h = _port_anchor_extent([p.name for p in outputs])
        pack_frame = _PackFrame(
            side_width=in_w + out_w,
            height_floor=max(in_h, out_h),
            pad_width=_TOP_FRAME_PAD_X,
            pad_height=_TOP_FRAME_PAD_Y,
            head=f"_in_{inputs[0].name}" if inputs else None,
        )
        pack_plan = _build_pack_plan(top, domain_map, reset_map, hints, pack_frame)

    if as_cluster_tree:
        # Cluster-tree emission: every instance with children becomes
        # a ``subgraph cluster_…`` so containment reads as
        # containment — u_dma's sub-cells live *inside* u_dma's box
        # rather than dangling below it with a parent→child arrow.
        # Parent→child edges drop entirely; CDC / RDC markers migrate
        # to the destination cluster's border via the ``cdc_on_node``
        # path that existed for the top frame's direct children.
        # Children are emitted largest-first (descendant count, then
        # source line span) so big sub-blocks land top-left within
        # the parent cluster — schematic skim convention.
        _emit_scope_children(
            top,
            out,
            domain_map,
            reset_map=reset_map,
            hints=hints,
            top_level=True,
            parent_cluster="cluster_top",
            compact_params=block_diagram,
            pack_plan=pack_plan,
            pack_head=pack_frame.head,
        )
    else:
        # Original box-and-arrow tree: each child is a flat node and
        # parent→child edges carry the port-pair / CDC / RDC labels.
        # Tests + the standalone ``--format dot`` rely on this shape.
        for child in top.children:
            _emit_node(child, out, domain_map, reset_map=reset_map)
        for child in top.children:
            _emit_edges(child, out, domain_map, reset_map=reset_map)

    if block_diagram:
        # Block mode subsumes the port → child heuristic: the
        # connectivity analyzer already reaches top ports as
        # first-class endpoints, and it traces slices, field selects
        # and assign aliases that the bare-identifier match cannot.
        # Running both would double every port edge. The dataflow,
        # CDC and RDC edges were rendered into ``edge_text`` above, in
        # this same order.
        out.write(edge_text)
        out.write("  }\n")
        return

    # Port → child signal-flow edges: when a child's port connection
    # has a bare-identifier net that matches a top input/output port
    # name, draw a thin clock-keyed edge between the port anchor and
    # the child. Clock and reset ports are skipped (they fan out to
    # nearly every flop and bury the data flow).
    _emit_port_signal_edges(top, out, domain_map, as_cluster_tree=as_cluster_tree)

    # CDC arrow: dashed-red overlay edge from input port anchor to
    # the child carrying an async crossing. Drawn AFTER the
    # signal-flow edges so the red arrow paints on top of the gray
    # one when both terminate at the same port + child pair.
    _emit_top_cdc_arrows(top, out, domain_map, as_cluster_tree=as_cluster_tree)

    # RDC arrow: dashed-orange overlay edge from the reset port anchor
    # to the child carrying an RDC crossing. Distinct color from the
    # CDC arrow so reviewers can tell clock-domain from reset-domain
    # crossings even when both terminate at the same destination.
    if reset_map is not None:
        _emit_top_rdc_arrows(top, out, reset_map, as_cluster_tree=as_cluster_tree)

    out.write("  }\n")


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# ``_CLOCK_NAME_RE`` / ``_RESET_NAME_RE`` now live in
# :mod:`rtl_buddy_view.connectivity` (imported above) so the analyzer
# and this renderer filter clock/reset trees by one shared definition.
# They keep their original names here — the fallback for when the
# domain map is absent, ``clocks[].ports`` being authoritative when
# one is supplied.
_NEUTRAL_EDGE_COLOR = "#cbd5e1"  # slate-300, falls back when no clock typing

#: Block-diagram dataflow edge accent. Tailwind ``slate-600`` — reads
#: as structural wiring next to the CDC red / RDC orange hazard
#: overlays rather than competing with them.
_BLOCK_EDGE_COLOR = "#475569"

#: De-emphasized (``rbsch: side``) dataflow edge. Tailwind
#: ``slate-400`` — present but visually behind the structural slate,
#: so the main path pops without the CSR wiring disappearing.
_SIDE_EDGE_COLOR = "#94a3b8"

#: Cap on net names rendered on one block-diagram edge label. Beyond
#: this the label shows the first ``MAX_BLOCK_EDGE_NETS`` names plus a
#: ``…(+N more)`` tail — same visual-truncation contract as
#: :data:`MAX_EDGE_LABEL_CONNECTIONS`.
MAX_BLOCK_EDGE_NETS = 3


def _emit_port_signal_edges(
    top: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    *,
    as_cluster_tree: bool = False,
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

    **Implicit connections**: the ``.port`` shorthand carries an empty
    ``net_expr_text``; the elaborator binds it to the same-named net,
    so we resolve it to ``port_name`` and trace it like any other bare
    identifier. Skipping it (the pre-fix behaviour) silently dropped
    every signal wired by shorthand.

    **Color**: when a domain map is present, edges are colored by
    the clock domain the signal lives in:
      * port_domains[] lookup for ports typed via
        ``set_input_delay -clock`` / ``set_output_delay -clock``
      * fall back to the destination/source child's predominant
        clock
      * fall back to a neutral slate when no clock is determinable.

    ``as_cluster_tree`` mirrors the caller's layout mode. ``lhead`` /
    ``ltail`` may only name a cluster that was actually emitted, so
    the attribute is attached solely in cluster mode — in the default
    box layout those clusters don't exist and Graphviz warns
    ``lhead cluster_… not found``.
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
            if not net and conn.port_name is not None:
                # Implicit ``.port`` shorthand — same-name net.
                net = conn.port_name
            if not _IDENTIFIER_RE.match(net):
                continue
            if (
                net in clock_ports
                or _CLOCK_NAME_RE.search(net)
                or _RESET_NAME_RE.search(net)
            ):
                continue
            # ``lhead`` / ``ltail`` clip the edge at the cluster
            # boundary under ``compound=true`` when the target/source
            # is a non-leaf child rendered as a cluster. Without it,
            # the edge punches through the cluster border to the
            # invisible anchor inside, which reads as "signal goes
            # into the middle of u_dma" instead of "signal lands at
            # u_dma's edge." Harmless attribute for box children
            # since their cluster id wasn't emitted.
            child_cluster = (
                _cluster_id_for(child.instance_path)
                if (as_cluster_tree and child.children)
                else None
            )
            if net in inputs and net not in seen_in:
                seen_in.add(net)
                color = _signal_edge_color(net, child, domain_map, port_clock)
                lhead = f', lhead="{child_cluster}"' if child_cluster else ""
                # ``tailport=e`` pins the edge's start to the east
                # (right) edge of the input-port anchor — lines up
                # with the ``▶`` glyph in the anchor label so the
                # arrow reads as exiting the port marker.
                out.write(
                    f'    "_in_{net}" -> "{child.instance_path}" '
                    f'[color="{color}", penwidth=1.2,'
                    f" arrowsize=0.6, tailport=e{lhead}];\n"
                )
            if net in outputs and net not in seen_out:
                seen_out.add(net)
                color = _signal_edge_color(net, child, domain_map, port_clock)
                ltail = f', ltail="{child_cluster}"' if child_cluster else ""
                # ``tailport=e`` pins the edge's start to the east
                # (right) edge of the source node/cluster, symmetric
                # with the ``tailport=e`` on input-port arrows.
                # ``headport=w`` pins the end at the west (left)
                # edge of the output-port anchor — lines up with the
                # ``▶`` glyph in the anchor label so the arrow reads
                # as entering the port marker.
                out.write(
                    f'    "{child.instance_path}" -> "_out_{net}" '
                    f'[color="{color}", penwidth=1.2,'
                    f" arrowsize=0.6, tailport=e, headport=w{ltail}];\n"
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


def _emit_connectivity_edges(
    top: HierNode,
    out: IO[str],
    module_table: ModuleTable | None,
    hints: HintMap | None = None,
    *,
    flags: Sequence[_RootFlag] | None = None,
) -> None:
    """Emit block-diagram dataflow edges for every resolved scope.

    ``flags`` are the root frame's :func:`_root_flags`; the top scope's
    port endpoints resolve to them (a hidden port's edges drop, a
    bundled port's edges land on its bundle flag). ``None`` computes
    them.

    Walks the whole tree, not just the top: a cluster with children of
    its own is a scope in its own right, and its internal wiring is
    exactly what a reader opens the box to see.

    Without a ``module_table`` the analyzer cannot resolve child pin
    directions, so nothing is emitted and block mode degrades to a
    plain cluster tree — the same graceful-degradation contract the
    overlays follow.
    """
    if module_table is None:
        return
    if flags is None:
        flags = _root_flags(top, module_table, hints)
    _emit_scope_connectivity(top, out, module_table, hints, is_top=True, flags=flags)


def _emit_scope_connectivity(
    node: HierNode,
    out: IO[str],
    table: ModuleTable,
    hints: HintMap | None,
    *,
    is_top: bool,
    flags: Sequence[_RootFlag] = (),
) -> None:
    """Emit one scope's sibling edges, then recurse into its children."""
    if node.module is not None and node.children:
        children = {
            child.instance.name: child
            for child in node.children
            if child.instance is not None
        }
        port_directions: Mapping[str, str | None] = (
            {f.key: f.direction for f in flags}
            if is_top
            else {p.name: p.direction for p in node.module.ports}
        )
        net_hints = (
            hints.nets_for_module(node.module_name) if hints is not None else None
        )
        pin_hints = hints.pin_directions_for(node.module) if hints is not None else None
        edges: Sequence[NetEdge] = scope_connectivity(
            node.module, table, net_hints=net_hints, pin_hints=pin_hints
        )
        if is_top:
            port_keys: dict[str, str | None] = {p.name: None for p in node.module.ports}
            for flag in flags:
                for member in flag.members:
                    port_keys[member] = flag.key
            if any(port_keys[p] != p for p in port_keys):
                edges = _regroup_root_edges(edges, port_keys)
        for edge in edges:
            src = _endpoint_ref(edge.src, children, port_directions, is_top=is_top)
            dst = _endpoint_ref(edge.dst, children, port_directions, is_top=is_top)
            if src is None or dst is None or src[0] == dst[0]:
                continue
            _write_block_edge(out, src, dst, edge)
    # Sorted by instance path (not emission-order complexity) — this
    # walk only decides *which edges appear where in the file*, and a
    # stable alphabetical order keeps diffs readable.
    for child in sorted(node.children, key=lambda c: c.instance_path):
        _emit_scope_connectivity(child, out, table, hints, is_top=False)


def _endpoint_ref(
    endpoint: Endpoint,
    children: dict[str, HierNode],
    port_directions: Mapping[str, str | None],
    *,
    is_top: bool,
) -> tuple[str, str | None] | None:
    """Resolve a connectivity endpoint to ``(node_id, cluster_id)``.

    ``cluster_id`` is non-None only when the endpoint is a non-leaf
    instance, i.e. one rendered as a ``subgraph cluster_…``; the caller
    turns it into ``ltail`` / ``lhead`` so the edge clips at the box
    border instead of punching through to the invisible anchor inside.

    Returns ``None`` when the endpoint has no drawable counterpart:
    an instance the hierarchy didn't build, or a port endpoint below
    the top frame — nested scopes have no port anchors, their cluster
    border *is* the port boundary.
    """
    kind, name = endpoint
    if kind == "inst":
        child = children.get(name)
        if child is None:
            return None
        cluster = _cluster_id_for(child.instance_path) if child.children else None
        return (child.instance_path, cluster)
    if not is_top:
        return None
    direction = port_directions.get(name)
    if direction in ("output", "inout"):
        return (f"_out_{name}", None)
    # Inputs and undecided ports (bidirectional flags) both anchor on
    # the source rank. At the top, ``port_directions`` is keyed by the
    # same :func:`_root_flags` the flag emitter draws, so every id
    # returned here is a declared flag.
    return (f"_in_{name}", None)


def _write_block_edge(
    out: IO[str],
    src: tuple[str, str | None],
    dst: tuple[str, str | None],
    edge: NetEdge,
) -> None:
    """Write one dataflow edge line.

    Edges attach IC-schematic style: data leaves a block at its east
    (right) side and enters the next block at its west (left) side,
    so every block reads inputs-on-the-left / outputs-on-the-right
    like a classic schematic symbol. Port anchors are the exception
    that proves the rule — they sit at the frame edge, so the edge
    always attaches on their frame-facing side (east for the
    ``_in_`` rank, west for the ``_out_`` rank) regardless of the
    edge's direction.
    """
    src_id, src_cluster = src
    dst_id, dst_cluster = dst
    # An edge flowing back to the left frame edge (an ``_in_`` anchor,
    # e.g. a bidirectional bus's return direction) exits its source's
    # west side — exiting east would route the wire back across the
    # source's own box.
    tailport = "w" if src_id.startswith("_out_") or dst_id.startswith("_in_") else "e"
    headport = "e" if dst_id.startswith("_in_") else "w"
    # ``rbsch`` emphasis (epic #159 phase 2): the main datapath gets
    # weight, side wiring thins and grays so the money path pops.
    # A named bundle (phase 3) is thick by default — one interface,
    # one visually solid wire — but an explicit per-net emphasis word
    # is the more specific statement and wins. Unhinted edges keep
    # the exact phase-1 attributes.
    if edge.emphasis == "main":
        edge_color, penwidth, arrowsize = _BLOCK_EDGE_COLOR, "2.2", "0.9"
    elif edge.emphasis == "side":
        edge_color, penwidth, arrowsize = _SIDE_EDGE_COLOR, "0.6", "0.6"
    elif edge.bundle is not None:
        edge_color, penwidth, arrowsize = _BLOCK_EDGE_COLOR, "1.6", "0.8"
    else:
        edge_color, penwidth, arrowsize = _BLOCK_EDGE_COLOR, "1.0", "0.7"
    attrs = [
        f'color="{edge_color}"',
        f"penwidth={penwidth}",
        f"arrowsize={arrowsize}",
        f"tailport={tailport}",
        f"headport={headport}",
    ]
    # A bundle renders under its author-given name — that name is the
    # whole point: one edge labeled ``cmd_bus`` instead of an N-net
    # list. The member nets stay in the JSON/ELK payloads.
    label = (
        _escape(edge.bundle) + r"\l"
        if edge.bundle is not None
        else _format_net_label(edge.nets)
    )
    if label:
        # ``xlabel`` rather than ``label``: under ``splines="ortho"``
        # Graphviz cannot place an edge label on the spline and warns
        # ("edge labels with splines=ortho not supported"), silently
        # falling back. An external label sits beside the edge and
        # routes cleanly.
        attrs.append(f'xlabel="{label}"')
        attrs.append("fontsize=9")
        attrs.append(f'fontcolor="{edge_color}"')
    if src_cluster:
        attrs.append(f'ltail="{src_cluster}"')
    if dst_cluster:
        attrs.append(f'lhead="{dst_cluster}"')
    out.write(f'    "{src_id}" -> "{dst_id}" [{", ".join(attrs)}];\n')


def _format_net_label(nets: tuple[str, ...]) -> str:
    """Render an edge's net list, truncated to :data:`MAX_BLOCK_EDGE_NETS`.

    Same ``\\l``-per-line / ``…(+N more)`` convention as the
    port-connection labels, so a wide bundle stays one narrow column
    instead of stretching the layout.
    """
    if not nets:
        return ""
    shown = nets[:MAX_BLOCK_EDGE_NETS]
    parts = [_escape(n) for n in shown]
    overflow = len(nets) - len(shown)
    if overflow:
        parts.append(f"…(+{overflow} more)")
    return r"\l".join(parts) + r"\l"


def _split_ports(
    top: HierNode,
    *,
    module_table: ModuleTable | None,
) -> tuple[list[Port], list[Port]]:
    """The top module's port anchors, split into source / sink ranks.

    The box-and-arrow and cluster-tree frames' anchors (interface ports
    flattened per signal); the block diagram's flags are
    :func:`_root_flags`. Shared by :func:`_emit_port_anchors` (which
    draws them) and :func:`_emit_top_frame` (which needs their extent
    to size the child grid) so the two can never disagree about what
    an anchor is.
    """
    if top.module is None or not top.module.ports:
        return ([], [])
    ports = flatten_interface_ports(top.module, module_table)
    inputs = [p for p in ports if p.direction == "input"]
    outputs = [p for p in ports if p.direction in ("output", "inout")]
    return (inputs, outputs)


#: Glyph on a boundary flag whose direction nothing settles — a
#: modport-less interface port no child binding orients, or a port
#: that declares no direction. It sits on the source rank.
_BIDIR_GLYPH = "↔"


def _boundary_directions(
    top: HierNode, module_table: ModuleTable | None
) -> dict[str, str | None]:
    """The direction each top port's flag faces in a block diagram.

    :func:`~rtl_buddy_view.connectivity.boundary_directions` when the
    table is at hand (modport-typed and modport-less interface ports
    resolved), the declared directions otherwise. Shared by the flag
    emitter and the edge resolver so a flag and the edges landing on
    it always agree on its id.
    """
    if top.module is None:
        return {}
    if module_table is None:
        return {p.name: p.direction for p in top.module.ports}
    return dict(boundary_directions(top.module, module_table))


def _emit_port_anchors(
    top: HierNode,
    out: IO[str],
    *,
    module_table: ModuleTable | None = None,
) -> None:
    """Emit input/output port markers in source/sink rank groups.

    The box-and-arrow and cluster-tree frames' anchors; the block
    diagram draws :func:`_root_flags` instead (:func:`_emit_root_flags`).

    Input ports anchor to ``rank=source`` (left edge under LR), output
    and inout ports to ``rank=sink`` (right edge). Anchors are
    ``plaintext`` shape so they read as labels, not boxes.

    Alignment within the label uses Graphviz line-terminators:
    ``\\r`` right-aligns input labels (arrows form a column on the
    right, closest to the children); ``\\l`` left-aligns output labels
    (arrows form a column on the left, closest to the children).

    When ``module_table`` is supplied, interface ports are flattened
    via :func:`flatten_interface_ports` so each interface signal
    surfaces as its own anchor with a real direction. Without the
    table the bundle port is dropped here (interface ports have
    ``direction=None``, which excludes them from the input/output
    filters) — matching the pre-#105 behaviour for back-compat with
    callers that don't have a table on hand. (#105.)
    """
    inputs, outputs = _split_ports(top, module_table=module_table)
    _write_flag_ranks(
        [(p.name, p.name, "▶") for p in inputs],
        [(p.name, p.name, "▶") for p in outputs],
        out,
    )


def _write_flag_ranks(
    inputs: Sequence[tuple[str, str, str]],
    outputs: Sequence[tuple[str, str, str]],
    out: IO[str],
) -> None:
    """Write the source / sink rank groups of ``(key, label, glyph)`` flags.

    Input flags are ``_in_<key>``, output flags ``_out_<key>``. An empty
    rank writes nothing, so a frame with no flags emits no rank group.
    """
    if inputs:
        # Right-edge alignment: pad each name on the LEFT to the max
        # name width so all ``▶`` glyphs land in the same column under
        # the monospace font. Padding has to be applied after
        # ``_escape`` since the escape pass would otherwise collapse
        # the padding spaces.
        max_w = max(len(_escape(label)) for _, label, _ in inputs)
        out.write("    { rank=source;\n")
        for key, label, glyph in inputs:
            padded = _escape(label).rjust(max_w)
            out.write(
                f'      "_in_{key}" '
                f'[shape=plaintext, label="{padded} {glyph}", fontsize=9];\n'
            )
        out.write("    }\n")
    if outputs:
        # Left-edge alignment: pad each name on the RIGHT instead, so
        # the ``▶`` glyph leading each output label forms a column.
        max_w = max(len(_escape(label)) for _, label, _ in outputs)
        out.write("    { rank=sink;\n")
        for key, label, glyph in outputs:
            padded = _escape(label).ljust(max_w)
            out.write(
                f'      "_out_{key}" '
                f'[shape=plaintext, label="{glyph} {padded}", fontsize=9];\n'
            )
        out.write("    }\n")


@dataclass(frozen=True)
class _RootFlag:
    """One boundary flag on a block diagram's root frame.

    ``key`` is the endpoint name the flag answers to: the port's own
    name, or ``$<bundle>`` for a flag standing for a root-port
    ``bundle=`` group — ``$`` can't start an SV identifier, so a bundle
    flag can never take a port's id. ``members`` are the ports it
    stands for, in declaration order.
    """

    key: str
    label: str
    direction: str | None
    members: tuple[str, ...]

    @property
    def is_output(self) -> bool:
        return self.direction in ("output", "inout")

    @property
    def node_id(self) -> str:
        return f"_out_{self.key}" if self.is_output else f"_in_{self.key}"


#: An edge line of this renderer's own output: ``"a" -> "b"``. The
#: same grammar ``tests/test_dot_declared_nodes.py`` checks.
_EDGE_LINE_RE = re.compile(r'^\s*"([^"]+)"\s*->\s*"([^"]+)"', re.M)


def _root_flags(
    top: HierNode, module_table: ModuleTable | None, hints: HintMap | None
) -> tuple[_RootFlag, ...]:
    """The root frame's boundary flags in block-diagram mode.

    Every port gets a flag (rtl-buddy-sch#195) on the side its
    :func:`_boundary_directions` entry says, with a port nothing
    settles drawn as a bidirectional ``↔`` flag on the source rank —
    never an undeclared node an edge would conjure up as a stray box.
    Two root-port hints reshape that (rtl-buddy-sch#196):

    * ``hide`` drops the port's flag; the edge resolver drops every
      edge to it, so nothing can name the missing flag.
    * Ports sharing a ``bundle=`` name share one flag, labelled with
      the bundle name and placed where its first member is declared.
      It faces the way the bundle's payload does — the same rule a
      net ``bundle=`` uses to fold a return path (bits when every
      voting member's width is known, port count otherwise); a tie,
      or no member with a direction, is a ``↔`` flag.

    Without root-port hints the result is exactly the pre-#196 flag
    list, one per port, in declaration order.
    """
    if top.module is None or not top.module.ports:
        return ()
    directions = _boundary_directions(top, module_table)
    port_hints = hints.nets_for_module(top.module_name) if hints is not None else {}
    ports = [p for p in top.module.ports if not _hint_attr(port_hints, p.name, "hide")]
    bundles: dict[str, list[Port]] = {}
    for p in ports:
        name = _hint_attr(port_hints, p.name, "bundle")
        if name:
            bundles.setdefault(name, []).append(p)
    bundle_keys = _bundle_flag_keys(sorted(bundles))
    params = module_param_defaults(top.module)
    flags: list[_RootFlag] = []
    placed: set[str] = set()
    for p in ports:
        name = _hint_attr(port_hints, p.name, "bundle")
        if not name:
            flags.append(
                _RootFlag(
                    key=p.name,
                    label=p.name,
                    direction=directions.get(p.name),
                    members=(p.name,),
                )
            )
            continue
        if name in placed:
            continue
        placed.add(name)
        members = bundles[name]
        flags.append(
            _RootFlag(
                key=bundle_keys[name],
                label=name,
                direction=_bundle_flag_direction(members, directions, params),
                members=tuple(m.name for m in members),
            )
        )
    return tuple(flags)


def _hint_attr(port_hints: Mapping[str, object], port: str, attr: str):
    """One field of a root port's net hint, or ``None``."""
    hint = port_hints.get(port)
    return getattr(hint, attr, None) if hint is not None else None


def _bundle_flag_keys(names: Sequence[str]) -> dict[str, str]:
    """``$<sanitised name>`` endpoint keys for root-port bundle flags.

    Sanitised to identifier characters so the node id is a plain
    quoted string; two names that sanitise alike (``a-b`` / ``a_b``)
    get a numeric suffix in sorted-name order, so each bundle keeps a
    flag of its own.
    """
    keys: dict[str, str] = {}
    used: set[str] = set()
    for name in names:
        base = "$" + re.sub(r"[^A-Za-z0-9_]", "_", name)
        key, n = base, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        used.add(key)
        keys[name] = key
    return keys


def _bundle_flag_direction(
    members: Sequence[Port],
    directions: Mapping[str, str | None],
    params: Mapping[str, str],
) -> str | None:
    """Which way a root-port bundle's flag faces: its payload's way.

    Inputs vote against outputs (``inout`` counts as output, the side
    an ``inout`` flag already sits on), weighted in bits when every
    voting member's width is known and by port count otherwise — the
    comparison :func:`~rtl_buddy_view.connectivity._fold_bundle_returns`
    uses to pick a bundle's data direction. A tie abstains: ``None``,
    drawn as a ``↔`` flag.
    """
    voters = [(m, directions.get(m.name)) for m in members]
    voters = [(m, d) for m, d in voters if d is not None]
    if not voters:
        return None
    widths = [port_width(m.type_text, params) for m, _ in voters]
    if all(w is not None for w in widths):
        weights = [w or 0 for w in widths]
    else:
        weights = [1] * len(voters)
    inward = sum(w for (_, d), w in zip(voters, weights) if d == "input")
    outward = sum(w for (_, d), w in zip(voters, weights) if d != "input")
    if inward > outward:
        return "input"
    if outward > inward:
        return "output"
    return None


def _emit_root_flags(flags: Sequence[_RootFlag], out: IO[str]) -> None:
    """Write the block diagram's root flags into their rank groups."""

    def glyph(flag: _RootFlag) -> str:
        return _BIDIR_GLYPH if flag.direction is None else "▶"

    _write_flag_ranks(
        [(f.key, f.label, glyph(f)) for f in flags if not f.is_output],
        [(f.key, f.label, glyph(f)) for f in flags if f.is_output],
        out,
    )


def _flag_ids_by_port(
    flags: Sequence[_RootFlag], top: HierNode
) -> dict[str, str | None]:
    """Root port name → the flag node id it draws as (``None``: hidden)."""
    ids: dict[str, str | None] = {}
    if top.module is not None:
        ids = {p.name: None for p in top.module.ports}
    for flag in flags:
        for member in flag.members:
            ids[member] = flag.node_id
    return ids


def _regroup_root_edges(
    edges: Sequence[NetEdge], port_keys: Mapping[str, str | None]
) -> list[NetEdge]:
    """Re-point root-port endpoints at the flags they draw as.

    ``port_keys`` maps each root port to its flag's endpoint key, or to
    ``None`` for a hidden port, whose edges are dropped. Ports re-keyed
    to one bundle flag can leave several edges between the same pair of
    endpoints; they merge into one (nets and pins unioned, ``main``
    beating ``side``, the bundle name kept only when every merged edge
    carries it). A bundle's return path then folds into its data
    direction exactly as a net ``bundle=`` does
    (:func:`~rtl_buddy_view.connectivity._fold_bundle_returns`), so an
    APB port group reads as one thick ``apb`` edge.
    """

    def remap(end: Endpoint) -> Endpoint | None:
        kind, name = end
        if kind != "port":
            return end
        key = port_keys.get(name, name)
        return None if key is None else ("port", key)

    grouped: dict[tuple[Endpoint, Endpoint], list[NetEdge]] = {}
    for edge in edges:
        src, dst = remap(edge.src), remap(edge.dst)
        if src is None or dst is None:
            continue
        grouped.setdefault((src, dst), []).append(
            dataclasses.replace(edge, src=src, dst=dst)
        )
    merged = [_merge_parallel(group) for _, group in sorted(grouped.items())]
    if any(e.bundle for e in merged):
        merged = _fold_bundle_returns(merged)
    return merged


def _merge_parallel(group: Sequence[NetEdge]) -> NetEdge:
    """One edge for several between the same pair of endpoints."""
    if len(group) == 1:
        return group[0]
    first = group[0]
    bits = [e.bits for e in group]
    emphases = {e.emphasis for e in group}
    bundles = {e.bundle for e in group}
    return dataclasses.replace(
        first,
        nets=tuple(sorted({n for e in group for n in e.nets})),
        src_pins=tuple(sorted({p for e in group for p in e.src_pins})),
        dst_pins=tuple(sorted({p for e in group for p in e.dst_pins})),
        bits=sum(b for b in bits if b is not None)
        if all(b is not None for b in bits)
        else None,
        bits_expr=None,
        emphasis="main"
        if "main" in emphases
        else ("side" if "side" in emphases else None),
        bundle=first.bundle if len(bundles) == 1 else None,
    )


def _emit_top_cdc_arrows(
    top: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    *,
    as_cluster_tree: bool = False,
    flag_ids: Mapping[str, str | None] | None = None,
) -> None:
    """Emit a dashed-red arrow from an input-port anchor to a child with
    a CDC crossing in. The src_clock must be the name of one of the
    top's input ports; otherwise the crossing is left silently visible
    only through the per-child ``[clock]`` label.

    ``as_cluster_tree`` gates ``lhead`` for the same reason it does in
    :func:`_emit_port_signal_edges`: the cluster it names only exists
    in cluster mode.

    ``flag_ids`` (block diagram) maps each root port to the flag it
    draws as: a bundled clock port's arrow leaves its bundle flag, and
    a hidden one's arrow is not drawn (the destination still carries
    its ``⚠CDC`` summary).
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
        child_cluster = (
            _cluster_id_for(child.instance_path)
            if (as_cluster_tree and child.children)
            else None
        )
        lhead = f', lhead="{child_cluster}"' if child_cluster else ""
        for c in domain_map.crossings_into(child.instance_path):
            if c.src_clock not in input_names:
                continue
            tail = _arrow_tail(c.src_clock, flag_ids)
            if tail is None:
                continue
            key = (c.src_clock, c.dst_clock)
            if key in seen:
                continue
            seen.add(key)
            label = f"⚠CDC: {_escape(c.src_clock)}→{_escape(c.dst_clock)}"
            out.write(
                f'    "{tail}" -> "{child.instance_path}" '
                f'[label="{label}", color="#dc2626", '
                f'style="dashed", fontcolor="#dc2626", tailport=e{lhead}];\n'
            )


def _subtree_size(node: HierNode) -> int:
    """Number of nodes in the subtree rooted at ``node`` (including itself).

    Cluster-tree ordering uses this as the primary sort key so larger
    sub-blocks float to the top of their parent's interior. The
    schematic-reading habit is "skim the big blocks first," and
    visually surfacing them top-left matches dot's LR layout.
    """
    return 1 + sum(_subtree_size(c) for c in node.children)


def _line_span(node: HierNode) -> int:
    """Source line span of the node's module declaration, or 0 if unknown.

    Tiebreaker for :func:`_subtree_size` so two same-shaped subtrees
    rank by amount-of-RTL inside them. Returns 0 for blackboxes /
    nodes the extractor never saw, which keeps them at the bottom of
    a tie cluster (a useful default — unresolved-module placeholders
    are usually the least interesting thing to read first).
    """
    if node.module is None or node.module.location is None:
        return 0
    loc = node.module.location
    if loc.start_line is None or loc.end_line is None:
        return 0
    return max(0, loc.end_line - loc.start_line)


def _total_line_count(node: HierNode) -> int:
    """Sum of ``_line_span`` for ``node`` and every descendant.

    Direct measure of "how much RTL is in this subtree" — wrappers
    with many submodules accumulate the same total as a single
    monolithic module of equivalent size, and a small wrapper
    around big children correctly ranks by its children. Source
    info is the same as :func:`_line_span` so blackboxes /
    location-less nodes contribute 0; the descendant-count
    fallback in :func:`_children_sorted_by_complexity` covers
    designs where the source-line info is unreliable.
    """
    return _line_span(node) + sum(_total_line_count(c) for c in node.children)


def _children_sorted_by_complexity(node: HierNode) -> tuple[HierNode, ...]:
    """Return ``node.children`` ordered for cluster-tree emission.

    Sort key (descending): ``(_total_line_count, _subtree_size)``.
    Final tiebreak by ``instance_path`` so the order is stable
    across runs even when both size keys collide. Graphviz roughly
    respects emission order for siblings under ``rankdir=LR``, so
    the first child emitted lands top-left within the parent
    cluster.

    Total line count is a more direct "amount of RTL" signal than
    raw descendant count: a 1000-line monolithic module and a
    wrapper around 10 ~100-line submodules read as equally heavy,
    which matches the schematic-skim intuition. Descendant count
    stays as a secondary key so designs whose extractor never
    populated source spans still get a sensible order.
    """
    return tuple(
        sorted(
            node.children,
            key=lambda c: (
                -_total_line_count(c),
                -_subtree_size(c),
                c.instance_path,
            ),
        )
    )


# --- landscape grid packing (rtl-buddy-sch#138) ----------------------
#
# In cluster-tree mode a scope's children carry no sibling edges (the
# containment box *is* the relationship), so dot gives every child the
# same rank and — under ``rankdir="LR"`` — stacks them in one column.
# A scope with N children is therefore N boxes tall and one box wide,
# which is how the hierarchy canvas ends up a portrait ribbon inside a
# landscape viewport.
#
# The rule: pack each scope's children into a *grid*. Pick the column
# count whose resulting scope comes closest to a landscape aspect,
# deal the children over the columns, and emit an invisible edge chain
# that puts each column on its own rank. The count is chosen per scope
# from the scope's own estimated geometry, so a scope of twelve skinny
# leaves and a scope of three deep sub-blocks get different answers —
# which is what "rankdir is fixed while scope shapes vary" asked for.
#
# Two things make the chain non-obvious:
#
# * dot splices a cluster's internal ranks into the *parent's* rank
#   sequence, so a child that is itself two columns wide spans two of
#   the parent's columns. Chaining the next column off such a child's
#   anchor (its first rank) would overlap the two, and overlapping
#   clusters are pushed apart along the other axis — undoing the pack.
#   So a column is chained from its deepest member's *last* rank; see
#   :class:`_NodeInfo.tail`.
# * a child with no port edge of its own would otherwise sink onto the
#   top frame's ``rank=source`` rank and share a column with the port
#   labels, so column 0 is chained off the first port anchor.
#
# The whole plan is computed once, bottom-up, in :func:`_build_pack_plan`
# — it is a pure function of the tree, so the emitted DOT stays
# byte-deterministic.

#: Aspect ratio (width / height) the packer aims each scope at. The
#: hub's canvas host measures ~1.7 on a 16:9 display.
_TARGET_ASPECT = 1.6

#: Courier advance width and line height at Graphviz's default 14pt.
_CHAR_W = 8.25
_LINE_H = 16.8

#: ``node [margin="0.4,0.06"]`` in points, both sides, plus Graphviz's
#: minimum node size (0.75in x 0.5in).
_NODE_PAD_X = 2 * 0.4 * 72.0
_NODE_PAD_Y = 2 * 0.06 * 72.0
_MIN_NODE_W = 54.0
_MIN_NODE_H = 36.0

#: The graph-level separations this renderer sets, in points.
_NODESEP_PT = 0.18 * 72.0
_RANKSEP_PT = 1.2 * 72.0

#: How much bigger a ``subgraph cluster_…`` is than the content it
#: wraps: the cluster margin, the band reserved for the title, and the
#: extra separation dot inserts at a cluster boundary. These are
#: *measured* — render a fixture, read the cluster ``bb``\s out of
#: ``dot -Tjson``, subtract the content extent — because Graphviz does
#: not expose the sum as one documented attribute. They land within a
#: few points on Graphviz 12.x, and the model only has to rank
#: candidate column counts against each other, never to predict the
#: final picture.
_CLUSTER_PAD_X_CONTENT = 40.0
_CLUSTER_PAD_X_LABEL = 16.0
_CLUSTER_PAD_Y = 63.0

#: A port anchor is a one-line ``plaintext`` node, so Graphviz's
#: minimum node height is what a whole rank of them costs, and it keeps
#: the default (not the node-level) horizontal margin.
_PORT_ANCHOR_H = _MIN_NODE_H
_PLAINTEXT_PAD_X = 2 * 0.11 * 72.0

#: What ``cluster_top`` and the graph pad add around everything else —
#: the frame's ``margin="20,20"``, its title band, and Graphviz's 4pt
#: graph border. Measured the same way as the cluster pads above. It
#: matters because it is a *constant* added to both axes: a portrait
#: scope is less portrait once it is framed, and ignoring it made the
#: packer keep single columns that the frame then stretched further.
_TOP_FRAME_PAD_X = 56.0
_TOP_FRAME_PAD_Y = 88.0


@dataclass(frozen=True)
class _PackFrame:
    """What sits *around* a scope's child grid, for the packer.

    Only the top frame has one: its port anchors flank the grid on the
    source and sink ranks, and ``head`` is the anchor column 0 chains
    off so it cannot drift back onto the source rank.
    """

    side_width: float = 0.0
    height_floor: float = 0.0
    pad_width: float = 0.0
    pad_height: float = 0.0
    head: str | None = None


@dataclass(frozen=True)
class _ScopePack:
    """One scope's children, dealt into columns.

    ``tails[c]`` is the instance path of the node occupying the last
    rank of column ``c`` — what the following column chains off.
    """

    columns: tuple[tuple[HierNode, ...], ...]
    tails: tuple[str, ...]


@dataclass(frozen=True)
class _NodeInfo:
    """A node's estimated geometry, as laid out in cluster-tree mode."""

    width: float
    height: float
    #: How many ranks the node's own layout consumes in its parent.
    span: int
    #: Instance path of a node sitting on the node's own last rank.
    tail: str


def _text_extent(label: str) -> tuple[float, float]:
    """Approximate (width, height) in points of a Graphviz label."""
    lines = [line for line in re.split(r"\\[lnr]", label) if line != ""]
    if not lines:
        lines = [""]
    return max(len(line) for line in lines) * _CHAR_W, len(lines) * _LINE_H


def _port_anchor_extent(names: Sequence[str]) -> tuple[float, float]:
    """Estimated (width contribution, height) of one port-anchor rank.

    The width is the anchor column plus the ``ranksep`` gap separating
    it from the child grid; the height is one minimum-height plaintext
    node per port.
    """
    if not names:
        return (0.0, 0.0)
    # Every anchor label is padded to the longest name and carries a
    # one-character direction marker (see :func:`_emit_port_anchors`).
    label_w = (max(len(n) for n in names) + 2) * _CHAR_W + _PLAINTEXT_PAD_X
    return (
        max(label_w, _MIN_NODE_W) + _RANKSEP_PT,
        len(names) * _PORT_ANCHOR_H + (len(names) - 1) * _NODESEP_PT,
    )


def _distribute(
    items: Sequence[tuple[HierNode, _NodeInfo]], columns: int
) -> tuple[tuple[tuple[HierNode, _NodeInfo], ...], ...]:
    """Deal ``items`` into ``columns`` columns, shortest column first.

    Walking the (already complexity-sorted) children in order and
    always appending to the currently shortest column balances column
    heights without reordering siblings — the largest-first reading
    order survives, it just wraps.
    """
    cols: list[list[tuple[HierNode, _NodeInfo]]] = [[] for _ in range(columns)]
    heights = [0.0] * columns
    for item in items:
        index = min(range(columns), key=lambda k: (heights[k], k))
        if cols[index]:
            heights[index] += _NODESEP_PT
        cols[index].append(item)
        heights[index] += item[1].height
    return tuple(tuple(col) for col in cols if col)


def _grid_extent(
    cols: Sequence[Sequence[tuple[HierNode, _NodeInfo]]],
) -> tuple[float, float]:
    """Estimated (width, height) of a packed column layout."""
    if not cols:
        return (0.0, 0.0)
    width = sum(max(info.width for _, info in col) for col in cols)
    width += (len(cols) - 1) * _RANKSEP_PT
    height = max(
        sum(info.height for _, info in col) + (len(col) - 1) * _NODESEP_PT
        for col in cols
    )
    return (width, height)


def _pack_columns(
    items: Sequence[tuple[HierNode, _NodeInfo]],
    frame: _PackFrame,
) -> tuple[tuple[tuple[HierNode, _NodeInfo], ...], ...]:
    """Choose the column count whose scope lands closest to landscape.

    Every count from 1 to ``len(items)`` is scored by the log-distance
    of its estimated aspect from :data:`_TARGET_ASPECT` — log so that
    2x too wide is penalised exactly as much as 2x too tall. Ties go to
    the smaller count, so a scope that is already landscape keeps the
    single column it has always rendered as.
    """
    if not items:
        return ()
    best: tuple[tuple[tuple[HierNode, _NodeInfo], ...], ...] = ()
    best_score = float("inf")
    for columns in range(1, len(items) + 1):
        candidate = _distribute(items, columns)
        width, height = _grid_extent(candidate)
        width += frame.side_width + frame.pad_width
        height = max(height, frame.height_floor) + frame.pad_height
        if height <= 0:
            continue
        score = abs(math.log((width / height) / _TARGET_ASPECT))
        if score < best_score - 1e-9:
            best, best_score = candidate, score
    return best


def _build_pack_plan(
    top: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    hints: HintMap | None,
    frame: _PackFrame,
) -> dict[str, _ScopePack]:
    """Decide every scope's column layout in one bottom-up pass.

    Keyed by instance path, so emission is a lookup rather than a
    re-derivation — the estimate is recursive (a cluster's size is its
    own packed interior) and recomputing it per scope would be
    quadratic for no benefit.

    A scope whose children carry ``rbsch`` ``rank=`` / ``group=`` hints
    is left in one column: the author has already laid that scope out
    and an explicit instruction outranks the heuristic.
    """
    plan: dict[str, _ScopePack] = {}

    def visit(node: HierNode, outer: _PackFrame) -> _NodeInfo:
        label_w, label_h = _text_extent(_label_for(node, domain_map, reset_map))
        if not node.children:
            return _NodeInfo(
                width=max(label_w + _NODE_PAD_X, _MIN_NODE_W),
                height=max(label_h + _NODE_PAD_Y, _MIN_NODE_H),
                span=1,
                tail=node.instance_path,
            )
        children = _children_sorted_by_complexity(node)
        items = [(child, visit(child, _PackFrame())) for child in children]
        if _has_layout_hints(node, hints):
            cols = _distribute(items, 1)
        else:
            cols = _pack_columns(items, outer)
        plan[node.instance_path] = _ScopePack(
            columns=tuple(tuple(child for child, _ in col) for col in cols),
            tails=tuple(
                max(col, key=lambda item: item[1].span)[1].tail for col in cols
            ),
        )
        inner_w, inner_h = _grid_extent(cols)
        return _NodeInfo(
            width=max(label_w + _CLUSTER_PAD_X_LABEL, inner_w + _CLUSTER_PAD_X_CONTENT),
            height=label_h + inner_h + _CLUSTER_PAD_Y,
            span=sum(max(info.span for _, info in col) for col in cols),
            tail=max(cols[-1], key=lambda item: item[1].span)[1].tail,
        )

    visit(top, frame)
    return plan


def _has_layout_hints(scope: HierNode, hints: HintMap | None) -> bool:
    """True when the author placed any ``rbsch`` layout hint in ``scope``."""
    if hints is None:
        return False
    for child in scope.children:
        hint = _layout_hint_for(scope, child, hints)
        if hint is not None and (hint.rank is not None or bool(hint.group)):
            return True
    return False


def _emit_column_chain(
    pack: _ScopePack | None, out: IO[str], *, head: str | None = None
) -> None:
    """Put each packed column on its own rank with invisible edges.

    Every child owns a node named by its instance path (a leaf box, or
    a cluster's invisible anchor), so one invisible edge per member of
    column *n + 1*, sourced from column *n*'s tail, makes dot rank the
    columns left to right. ``weight`` is left at the default: the chain
    wraps the column, it does not out-shout the real port edges.

    No ``rank=same`` statement is emitted — cluster members may not
    share one, and this renderer's bar is zero Graphviz warnings.
    """
    if pack is None or not pack.columns:
        return
    if head is not None:
        for child in pack.columns[0]:
            out.write(f'    "{head}" -> "{child.instance_path}" [style=invis];\n')
    for index, column in enumerate(pack.columns[1:]):
        source = pack.tails[index]
        for child in column:
            out.write(f'    "{source}" -> "{child.instance_path}" [style=invis];\n')


def _cluster_id_for(instance_path: str) -> str:
    """Sanitize an instance path into a Graphviz cluster name.

    ``subgraph cluster_…`` names must be alphanumeric + underscore;
    instance paths are dot-separated. Replace every non-alphanumeric
    char with ``_`` — collisions are impossible because instance
    paths are unique by construction (extractor guarantees) and the
    replacement is bijective for dotted ids.
    """
    return "cluster_" + re.sub(r"[^A-Za-z0-9_]", "_", instance_path)


def _emit_cluster_subtree(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    *,
    reset_map: ResetDomainMap | None = None,
    top_level: bool = False,
    parent_group: str | None = None,
    compact_params: bool = False,
    hints: HintMap | None = None,
    pack_plan: dict[str, _ScopePack] | None = None,
) -> None:
    """Recursively emit ``node`` and its descendants.

    Leaves render as a regular DOT node box (delegated to
    :func:`_emit_node`).

    Non-leaves render as a Graphviz cluster subgraph: the instance
    label becomes the cluster's title, the cluster's outline picks
    up the same clock-keyed / CDC / RDC styling that the box form
    would have carried, and the children render recursively
    *inside* the cluster — so the schematic reads "u_dma contains
    u_dma.u_wb which contains u_dma.u_wb.i_sync_rd2wr" by
    containment rather than as a chain of arrows.

    ``top_level=True`` carries the same semantics ``cdc_on_node``
    has for boxes: the destination of a CDC / RDC arrow from the
    top-frame's port anchors lands on this cluster, so the cluster
    border picks up the warning color.
    """
    if not node.children:
        _emit_node(
            node,
            out,
            domain_map,
            cdc_on_node=top_level,
            reset_map=reset_map,
            group=parent_group,
            compact_params=compact_params,
        )
        return

    cluster_id = _cluster_id_for(node.instance_path)
    # Reuse the box-form label builder so cluster titles stay
    # byte-identical to what the same instance would render as a
    # box: ``\l`` left-justified lines, padded param parens kept
    # column-aligned (``_label_for`` only ``_escape``\s atomic
    # components, never the pre-padded param block — re-escaping the
    # padded block would collapse the whitespace runs and break
    # alignment).
    title = _label_for(node, domain_map, reset_map)

    # Border colour resolution mirrors the box form's precedence:
    # CDC red > RDC orange > reset-synchroniser teal > clock-keyed
    # outline > neutral slate. The cluster carries the cue that a
    # box would have carried for the same instance.
    fill, _ = _fill_for(node, domain_map)
    is_sync = (
        reset_map is not None and node.instance_path in reset_map.synchronizer_paths()
    )
    cdc_summary = _format_cdc_summary(node, domain_map) if top_level else ""
    rdc_summary = (
        _format_rdc_summary(node, reset_map)
        if top_level and reset_map is not None
        else ""
    )
    outline = "#94a3b8"
    pen = 1
    fontcolor = None
    if cdc_summary:
        outline = "#dc2626"
        pen = 2
        fontcolor = "#dc2626"
    elif rdc_summary:
        outline = _RDC_COLOR
        pen = 2
        fontcolor = _RDC_COLOR
    elif is_sync:
        outline = _RSTSYNC_OUTLINE
        pen = 2
    elif fill != _DEFAULT_FILL and fill != _BLACKBOX_FILL and ":" not in fill:
        # Single-clock-typed subtree: pick up the clock palette for
        # the cluster outline so the domain reads at a glance.
        outline = fill

    label_suffix_parts: list[str] = []
    if cdc_summary:
        label_suffix_parts.append(cdc_summary)
    if rdc_summary:
        label_suffix_parts.append(rdc_summary)
    if label_suffix_parts:
        # ``_label_for`` already ends with ``\l``; append the CDC /
        # RDC summary lines as additional left-aligned suffix lines.
        title = title + "\\l".join(_escape(p) for p in label_suffix_parts) + "\\l"

    out.write(f"  subgraph {cluster_id} {{\n")
    if compact_params:
        # Block-diagram cluster titles use the compact HTML-table
        # label (parameters as two-column rows) — the CDC / RDC
        # summary lines ride along as full-width tag rows.
        html_title = _html_compact_label(
            node, domain_map, reset_map, extra_lines=tuple(label_suffix_parts)
        )
        out.write(f"    label=<{html_title}>;\n")
    else:
        out.write(f'    label="{title}";\n')
    out.write('    labelloc="t";\n')
    # ``labeljust="l"`` pins the label block to the left edge of the
    # cluster so the title text aligns flush-left under the top
    # border, matching the schematic convention. Without it the
    # block centers within the cluster width and stops reading as a
    # block title.
    out.write('    labeljust="l";\n')
    out.write('    style="rounded";\n')
    out.write(f'    color="{outline}";\n')
    out.write(f"    penwidth={pen};\n")
    if fontcolor:
        out.write(f'    fontcolor="{fontcolor}";\n')
    # Invisible anchor node, named with the instance path so any edge
    # elsewhere in the diagram that targets ``"<instance_path>"`` (port
    # signal-flow arrows, top-frame CDC/RDC overlays) resolves to a
    # real point *inside* this cluster instead of triggering Graphviz's
    # auto-create-undefined-node fallback — which would draw a second
    # box next to the cluster for the same instance.
    #
    # ``group=<parent_cluster>`` puts the anchor in the same vertical
    # column as its sibling anchors under ``rankdir=LR`` — dot's
    # documented knob for "stack these in one column." Combined with
    # emission order it biases the layout to render the children
    # top-down in the sorted order.
    group_attr = f', group="{parent_group}"' if parent_group else ""
    out.write(
        f'    "{node.instance_path}" [shape=point, style=invis,'
        f" width=0, height=0{group_attr}];\n"
    )
    _emit_scope_children(
        node,
        out,
        domain_map,
        reset_map=reset_map,
        hints=hints,
        top_level=False,
        parent_cluster=cluster_id,
        compact_params=compact_params,
        pack_plan=pack_plan,
    )
    out.write("  }\n")


def _layout_hint_for(scope: HierNode, child: HierNode, hints: HintMap | None):
    """The child instance's box hints, looked up from its parent scope."""
    if hints is None or child.instance is None:
        return None
    return hints.for_instance(scope.module_name, child.instance.name)


def _group_cluster_id(scope_path: str, name: str) -> str:
    """Cluster id for one ``rbsch`` layout-group container.

    Scoped by the parent instance path so the same group name in two
    scopes yields two containers, and prefixed distinctly from
    :func:`_cluster_id_for` ids so a group can never collide with a
    real instance's cluster.
    """
    return (
        "cluster_grp_"
        + re.sub(r"[^A-Za-z0-9_]", "_", scope_path)
        + "__"
        + re.sub(r"[^A-Za-z0-9_]", "_", name)
    )


def _emit_scope_children(
    scope: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    *,
    reset_map: ResetDomainMap | None,
    hints: HintMap | None,
    top_level: bool,
    parent_cluster: str,
    compact_params: bool,
    pack_plan: dict[str, _ScopePack] | None = None,
    pack_head: str | None = None,
) -> None:
    """Emit one scope's children, honoring ``rbsch`` layout hints.

    Children sharing a ``group=<name>`` hint emit inside one dashed
    container subgraph — a *virtual* hierarchy level: related
    siblings boxed together without a wrapper module existing in the
    RTL (the epic's ``cclk_domain`` case). The container opens where
    its first member lands in the complexity-sorted order, so group
    members stay together while everything else keeps the
    established largest-first layout. Members keep their own
    Graphviz ``group=`` *column* attribute (`parent_cluster`), which
    is an unrelated dot knob despite the name.

    After the children, ``rank=`` hints become a chain of invisible
    ordering edges (:func:`_emit_rank_chain`).

    ``pack_plan`` (rtl-buddy-sch#138) is the landscape grid decided by
    :func:`_build_pack_plan`; passing it wraps an otherwise edge-free
    sibling column into a grid. It is ``None`` unless the caller knows
    the scope has no sibling dataflow to rank by — the block diagram
    has its own ranking and must not be second-guessed. ``pack_head``
    is the top frame's first port anchor (see
    :func:`_emit_column_chain`).
    """
    members: dict[str, list[HierNode]] = {}
    for child in _children_sorted_by_complexity(scope):
        hint = _layout_hint_for(scope, child, hints)
        if hint is not None and hint.group:
            members.setdefault(hint.group, []).append(child)

    emitted: set[str] = set()
    for child in _children_sorted_by_complexity(scope):
        hint = _layout_hint_for(scope, child, hints)
        name = hint.group if hint is not None and hint.group else None
        if name is None:
            _emit_cluster_subtree(
                child,
                out,
                domain_map,
                reset_map=reset_map,
                top_level=top_level,
                parent_group=parent_cluster,
                compact_params=compact_params,
                hints=hints,
                pack_plan=pack_plan,
            )
            continue
        if name in emitted:
            continue
        emitted.add(name)
        out.write(f"  subgraph {_group_cluster_id(scope.instance_path, name)} {{\n")
        out.write(f'    label="{_escape(name)}";\n')
        out.write('    labelloc="t";\n')
        out.write('    labeljust="l";\n')
        # Dashed + neutral: a virtual container must read as an
        # annotation, never as a real module boundary.
        out.write('    style="rounded,dashed";\n')
        out.write('    color="#94a3b8";\n')
        out.write("    penwidth=1;\n")
        for member in members[name]:
            _emit_cluster_subtree(
                member,
                out,
                domain_map,
                reset_map=reset_map,
                top_level=top_level,
                parent_group=parent_cluster,
                compact_params=compact_params,
                hints=hints,
                pack_plan=pack_plan,
            )
        out.write("  }\n")

    # ``emitted`` is non-empty only when a ``group=`` hint put some
    # children inside a virtual container. The plan already leaves such
    # a scope in one column, but the head chain would still reach into
    # the container — so stand down from the scope entirely and let the
    # author's grouping be the whole layout.
    if pack_plan is not None and not emitted:
        _emit_column_chain(pack_plan.get(scope.instance_path), out, head=pack_head)
    _emit_rank_chain(scope, out, hints)


def _emit_rank_chain(scope: HierNode, out: IO[str], hints: HintMap | None) -> None:
    """Realize ``rank=`` hints as invisible ordering edges.

    Every child (leaf box or cluster anchor) owns a node named by its
    instance path, so an invisible edge between successive rank
    groups biases dot's rank assignment left→right in hint order —
    each member of rank *n* points at the first member of rank
    *n + 1*. A bias, not a guarantee: real dataflow edges still
    participate in ranking, which is exactly right — the author is
    ordering the pipeline, not overruling its wiring. Same-rank
    members share the same upstream/downstream constraints but no
    ``rank=same`` statement is emitted: cluster members can't legally
    share a rank statement across cluster boundaries, and the
    acceptance bar for these figures is zero Graphviz warnings.
    """
    ranked: dict[int, list[str]] = {}
    for child in scope.children:
        hint = _layout_hint_for(scope, child, hints)
        if hint is not None and hint.rank is not None:
            ranked.setdefault(hint.rank, []).append(child.instance_path)
    if len(ranked) < 2:
        return
    ordered = sorted(ranked.items())
    for (_, current), (_, following) in zip(ordered, ordered[1:]):
        target = sorted(following)[0]
        for member in sorted(current):
            out.write(f'    "{member}" -> "{target}" [style=invis, weight=8];\n')


def _emit_node(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    *,
    cdc_on_node: bool = False,
    reset_map: ResetDomainMap | None = None,
    group: str | None = None,
    compact_params: bool = False,
) -> None:
    """Emit a node line.

    ``cdc_on_node`` is set for direct children of the top frame: those
    children would normally carry their CDC / RDC markers on the
    incoming edge from the top, but in cluster mode that edge doesn't
    exist — so the markers collapse onto the node itself (label
    suffix + accent border).

    ``compact_params`` (block-diagram mode) swaps the text label for
    the compact HTML-table form — parameters as two-column rows
    instead of a multi-line ``#(…)`` block. Fill, stripe, dash and
    border styling are untouched: the table is layout-only.
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
        _emit_html_grid_node(
            node, out, domain_map, pairs, cdc_on_node=cdc_on_node, reset_map=reset_map
        )
        for child in node.children:
            _emit_node(
                child,
                out,
                domain_map,
                reset_map=reset_map,
                compact_params=compact_params,
            )
        return

    label = _label_for(node, domain_map, reset_map)
    attrs = [f'label="{label}"']
    fill, striped = _fill_for(node, domain_map)
    is_sync = (
        reset_map is not None and node.instance_path in reset_map.synchronizer_paths()
    )
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
    label_suffix = ""
    has_border = False
    if cdc_on_node:
        cdc_summary = _format_cdc_summary(node, domain_map)
        if cdc_summary:
            label_suffix = f"\\n{cdc_summary}"
            attrs.append('color="#dc2626"')
            attrs.append("penwidth=2")
            attrs.append('fontcolor="#dc2626"')
            has_border = True
    if cdc_on_node and reset_map is not None:
        rdc_summary = _format_rdc_summary(node, reset_map)
        if rdc_summary:
            label_suffix += f"\\n{rdc_summary}"
            # Don't override an existing CDC border — the dual-issue
            # node keeps its red CDC border and surfaces the RDC issue
            # via the label only. When CDC isn't present, RDC drives
            # the border on its own.
            if not has_border:
                attrs.append(f'color="{_RDC_COLOR}"')
                attrs.append("penwidth=2")
                attrs.append(f'fontcolor="{_RDC_COLOR}"')
                has_border = True
    if label_suffix:
        attrs[0] = f'label="{label}{label_suffix}"'
    if compact_params:
        # The suffix summaries become full-width tag rows in the
        # table; border / fontcolor cues set above stay on the node
        # attrs and color the HTML text the same way.
        summary_lines: list[str] = []
        if cdc_on_node:
            cdc_line = _format_cdc_summary(node, domain_map)
            if cdc_line:
                summary_lines.append(cdc_line)
            if reset_map is not None:
                rdc_line = _format_rdc_summary(node, reset_map)
                if rdc_line:
                    summary_lines.append(rdc_line)
        html = _html_compact_label(
            node, domain_map, reset_map, extra_lines=tuple(summary_lines)
        )
        attrs[0] = f"label=<{html}>"
    # Synchronizer marker: teal outline + slightly thicker pen. Only
    # applied when no warning border is already in place — clock /
    # reset issues take precedence visually since they're hazards.
    if is_sync and not has_border:
        attrs.append(f'color="{_RSTSYNC_OUTLINE}"')
        attrs.append("penwidth=2")
    if group:
        attrs.append(f'group="{group}"')
    out.write(f'  "{node.instance_path}" [{", ".join(attrs)}];\n')
    for child in node.children:
        _emit_node(
            child, out, domain_map, reset_map=reset_map, compact_params=compact_params
        )


def _html_compact_label(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
    extra_lines: tuple[str, ...] = (),
) -> str:
    """Compact HTML-table label for block-diagram nodes and clusters.

    The text form renders parameter overrides as a ``#(…)`` block —
    one line per parameter plus two bracket lines, the tallest thing
    in a typical diagram. Here each parameter becomes a slim
    two-column row (name | value) at a reduced point size, so a
    two-parameter instance costs two rows instead of four monospace
    lines. Clock / reset tags, the rstsync marker, the blackbox
    marker and any ``extra_lines`` (CDC / RDC summaries) follow as
    full-width tag rows.

    The table is layout-only (``BORDER="0"``): fill, stripe, dash,
    border and fontcolor stay node/cluster attributes, so all the
    styling precedence rules are shared with the text-label path.
    Content cleaning matches ``_escape`` (whitespace runs collapse)
    before HTML entities are applied.
    """

    def cell_text(s: str) -> str:
        return _html_escape(" ".join(s.split()))

    inst_name = node.instance.name if node.instance is not None else node.module_name
    rows: list[str] = []
    if node.display_label:
        # The author's role name is the block title; bold separates it
        # from the instance / module rows that follow it.
        rows.append(
            f'<TR><TD COLSPAN="2" ALIGN="LEFT"><B>'
            f"{cell_text(node.display_label)}</B></TD></TR>"
        )
    rows.extend(
        (
            f'<TR><TD COLSPAN="2" ALIGN="LEFT">{cell_text(inst_name)}</TD></TR>',
            f'<TR><TD COLSPAN="2" ALIGN="LEFT">{cell_text(node.module_name)}</TD></TR>',
        )
    )
    for ov in node.shown_param_overrides:
        value = f'<FONT POINT-SIZE="10">{cell_text(ov.value_text)}</FONT>'
        if ov.param_name is None:
            # Positional override — no name column to fill.
            rows.append(f'<TR><TD COLSPAN="2" ALIGN="LEFT">{value}</TD></TR>')
        else:
            name = (
                f'<FONT POINT-SIZE="10">{cell_text(ov.param_name)}&nbsp;&nbsp;</FONT>'
            )
            rows.append(
                f'<TR><TD ALIGN="LEFT">{name}</TD><TD ALIGN="LEFT">{value}</TD></TR>'
            )
    tags: list[str] = []
    if domain_map is not None:
        clocks = _clocks_under(node.instance_path, domain_map)
        if clocks:
            tags.append(f"[{', '.join(clocks)}]")
    if reset_map is not None:
        resets = _resets_under(node.instance_path, reset_map)
        if resets:
            tags.append(f"[{', '.join(resets)}]")
        if node.instance_path in reset_map.synchronizer_paths():
            tags.append("✓rstsync")
    if node.is_blackbox:
        tags.append("(blackbox)")
    tags.extend(extra_lines)
    for tag in tags:
        rows.append(
            f'<TR><TD COLSPAN="2" ALIGN="LEFT">'
            f'<FONT POINT-SIZE="10">{cell_text(tag)}</FONT></TD></TR>'
        )
    return (
        '<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="1">'
        + "".join(rows)
        + "</TABLE>"
    )


def _emit_html_grid_node(
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None,
    pairs: tuple[tuple[str, str], ...],
    *,
    cdc_on_node: bool,
    reset_map: ResetDomainMap | None = None,
) -> None:
    """Emit a node as an HTML-label table — header text + one row per crossing.

    Used for nodes that have multiple distinct ``(src, dst)`` crossing
    pairs (typically gray-code CDC FIFOs that cross both ways). Dot's
    ``striped`` style only emits vertical bands; HTML labels let us
    emit a 2-column grid where each row shows one direction.

    The header cell carries the same text content as a regular node
    label (instance, module, parameter block, clock summary, reset
    summary, blackbox marker), using ``<BR ALIGN="LEFT"/>`` for
    left-aligned line breaks so it visually matches the rest of the
    diagram.
    """
    text_lines = _label_text_lines(node, domain_map, reset_map)
    header = '<BR ALIGN="LEFT"/>'.join(_html_escape(ln) for ln in text_lines)
    # Trailing <BR/> anchors the last line to the left, matching the
    # ``\l`` behavior in plain labels.
    header += '<BR ALIGN="LEFT"/>'

    rows: list[str] = [
        f'<TR><TD COLSPAN="2" BORDER="0" ALIGN="LEFT">'
        f'<FONT FACE="Courier,monospace">{header}</FONT></TD></TR>'
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


def _label_text_lines(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> list[str]:
    """Return the per-line text content that ``_label_for`` would render,
    minus the ``\\l`` joining — useful for HTML-label emission.
    """
    inst_name = node.instance.name if node.instance is not None else node.module_name
    lines = [inst_name, node.module_name]
    if node.display_label:
        # Same precedence as ``_label_for``: the author's role name
        # leads, so the multi-crossing grid form doesn't silently
        # drop a label the plain box would show.
        lines.insert(0, node.display_label)
    if node.shown_param_overrides:
        # Param block already carries ``\l`` separators; flatten to a
        # list of bare lines for HTML conversion.
        block = _format_param_overrides(node.shown_param_overrides)
        # Re-split on the ``\l`` markers; each is a literal two-char
        # ``\\l`` in the formatter output.
        lines.extend(s for s in block.split(r"\l") if s)
    if domain_map is not None:
        clocks = _clocks_under(node.instance_path, domain_map)
        if clocks:
            lines.append(f"[{', '.join(clocks)}]")
    if reset_map is not None:
        resets = _resets_under(node.instance_path, reset_map)
        if resets:
            lines.append(f"[{', '.join(resets)}]")
        if node.instance_path in reset_map.synchronizer_paths():
            lines.append("✓rstsync")
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
    """The colour of ``clock_name`` in the figure being rendered.

    Read from the assignment :func:`figure_clock_colors` fixed for this
    render, so every place one figure colours a clock agrees. Outside a
    render, or for a clock the assignment doesn't know, the stable name
    hash (:func:`rtl_buddy_view.clock_colors.hash_color`) — deterministic
    across processes and ``PYTHONHASHSEED`` values either way.
    """
    colors = _FIGURE_COLORS.get()
    if colors is not None:
        color = colors.get(clock_name)
        if color is not None:
            return color
    return hash_color(clock_name)


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


def _resets_under(instance_path: str, reset_map: ResetDomainMap) -> tuple[str, ...]:
    """All distinct ``<reset><arrow>`` tags present under ``instance_path``,
    sorted alphabetically.

    Parallel to :func:`_clocks_under`. The arrow encodes the *flop's*
    inferred polarity (down = active-low, up = active-high) — a port
    declared low whose consumer uses it inverted produces an ``↑``
    tag, surfacing the polarity-mismatch hazard in the rendered
    label as well as in the RDC summary.

    Reset-tree side-graph emission for source-side (not flop-side)
    reset annotation is deferred to a follow-up PR (#3 subtask 4
    ``reset-tree-sidegraph`` bullet).
    """
    prefix = instance_path + "."
    found: set[str] = set()
    for flop in reset_map.flop_resets:
        if flop.instance_path == instance_path or flop.instance_path.startswith(prefix):
            arrow = "↓" if flop.polarity == "low" else "↑"
            found.add(f"{flop.reset}{arrow}")
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
    node: HierNode,
    out: IO[str],
    domain_map: DomainMap | None = None,
    *,
    reset_map: ResetDomainMap | None = None,
) -> None:
    for child in node.children:
        attr_parts: list[str] = []
        port_label = (
            _format_port_connections(child.instance.port_connections)
            if child.instance is not None and child.instance.port_connections
            else ""
        )
        cdc_summary = _format_cdc_summary(child, domain_map)
        rdc_summary = (
            _format_rdc_summary(child, reset_map) if reset_map is not None else ""
        )
        label_parts: list[str] = []
        if cdc_summary:
            label_parts.append(cdc_summary)
        if rdc_summary:
            label_parts.append(rdc_summary)
        if port_label:
            label_parts.append(port_label)
        label = "\\n".join(label_parts)
        if label:
            attr_parts.append(f'label="{label}"')
        if cdc_summary:
            # CDC is the worse of the two hazards (silicon-failing
            # metastability vs. reset-deassert metastability) — when
            # both apply on the same edge, the CDC red wins on the
            # edge style and the RDC marker sits in the label only.
            attr_parts.append('color="#dc2626"')
            attr_parts.append('style="dashed"')
            attr_parts.append('fontcolor="#dc2626"')
        elif rdc_summary:
            # Pure RDC edge: dashed orange so it's distinguishable
            # from the CDC red at a glance.
            attr_parts.append(f'color="{_RDC_COLOR}"')
            attr_parts.append('style="dashed"')
            attr_parts.append(f'fontcolor="{_RDC_COLOR}"')
        # AXI bundles are intentionally NOT styled here: perf is
        # surfaced in the viewer's AXI tab and in json_render, not on
        # the DOT graph (the #69 pivot away from per-edge styling).
        # ``render`` ``del``s its ``axi_perf_map`` and never forwards
        # it to this function — see the note at the top of ``render``.
        attrs = f" [{', '.join(attr_parts)}]" if attr_parts else ""
        out.write(f'  "{node.instance_path}" -> "{child.instance_path}"{attrs};\n')
        _emit_edges(
            child,
            out,
            domain_map,
            reset_map=reset_map,
        )


def _format_rdc_summary(child: HierNode, reset_map: ResetDomainMap | None) -> str:
    """Build the ``⚠RDC: rst_n:async-deassert`` label fragment for an RDC edge.

    Returns an empty string when no reset crossing terminates at
    ``child``. Multiple crossings collapse to a deterministic
    ``(reset, kind)`` list — matches the tree renderer's marker shape
    so reviewers see the same summary across formats.
    """
    if reset_map is None:
        return ""
    crossings = reset_map.crossings_into(child.instance_path)
    if not crossings:
        return ""
    parts = sorted({f"{c.reset}:{c.kind}" for c in crossings})
    return "⚠RDC: " + ", ".join(parts)


def _emit_top_rdc_arrows(
    top: HierNode,
    out: IO[str],
    reset_map: ResetDomainMap,
    *,
    as_cluster_tree: bool = False,
    flag_ids: Mapping[str, str | None] | None = None,
) -> None:
    """Emit a dashed-orange arrow from a reset-port anchor to a child with
    an RDC crossing in. The reset name must match one of the top's
    input ports; otherwise the crossing is left visible only through
    the destination child's label suffix.

    ``as_cluster_tree`` gates ``lhead`` — see
    :func:`_emit_port_signal_edges`. ``flag_ids`` re-points the tail at
    a block diagram's flags — see :func:`_emit_top_cdc_arrows`.
    """
    if top.module is None:
        return
    input_names = {p.name for p in top.module.ports if p.direction == "input"}
    for child in top.children:
        seen: set[tuple[str, str]] = set()
        child_cluster = (
            _cluster_id_for(child.instance_path)
            if (as_cluster_tree and child.children)
            else None
        )
        lhead = f', lhead="{child_cluster}"' if child_cluster else ""
        for c in reset_map.crossings_into(child.instance_path):
            if c.reset not in input_names:
                continue
            tail = _arrow_tail(c.reset, flag_ids)
            if tail is None:
                continue
            key = (c.reset, c.kind)
            if key in seen:
                continue
            seen.add(key)
            label = f"⚠RDC: {_escape(c.reset)}:{_escape(c.kind)}"
            out.write(
                f'    "{tail}" -> "{child.instance_path}" '
                f'[label="{label}", color="{_RDC_COLOR}", '
                f'style="dashed", fontcolor="{_RDC_COLOR}", tailport=e{lhead}];\n'
            )


def _arrow_tail(port: str, flag_ids: Mapping[str, str | None] | None) -> str | None:
    """The node an overlay arrow from root input ``port`` leaves from."""
    if flag_ids is None:
        return f"_in_{port}"
    return flag_ids.get(port, f"_in_{port}")


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


def _label_for(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None = None,
) -> str:
    """Build a left-aligned, fixed-width node label.

    Lines stack via ``\\l`` (Graphviz left-aligned newline) so the
    instance name, module name, parameter block, clock tag, reset
    tag, sync-marker, and any blackbox marker all flush against the
    left edge of the box — matches the param-block alignment and
    reads naturally under the monospace font.

    The clock tag enumerates every clock present in the subtree
    (e.g. ``[a_clk, s_clk, vu_clk]``), not just the predominant one
    — a single-clock label on a multi-clock module hides the
    crossings that the diagram is meant to surface. The reset tag
    follows the same shape with polarity arrows
    (``[rst_n↓, por_n↓]``).

    An author-supplied ``display_label`` (``// rbsch: label="…"``,
    epic #159) leads: in a documentation figure the role name is the
    title and the instance / module names demote to subtitles. Nodes
    without one render exactly as before.
    """
    inst_name = node.instance.name if node.instance is not None else node.module_name
    lines = [_escape(inst_name), _escape(node.module_name)]
    if node.display_label:
        lines.insert(0, _escape(node.display_label))
    if node.shown_param_overrides:
        lines.append(_format_param_overrides(node.shown_param_overrides))
    if domain_map is not None:
        clocks = _clocks_under(node.instance_path, domain_map)
        if clocks:
            lines.append(f"[{', '.join(_escape(c) for c in clocks)}]")
    if reset_map is not None:
        resets = _resets_under(node.instance_path, reset_map)
        if resets:
            lines.append(f"[{', '.join(_escape(r) for r in resets)}]")
        if node.instance_path in reset_map.synchronizer_paths():
            lines.append("✓rstsync")
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
