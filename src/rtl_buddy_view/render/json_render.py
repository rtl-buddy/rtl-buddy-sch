"""``view.json`` v1 renderer (Phase 4 — #17).

Emits a deterministic, schema-versioned JSON representation of the
hierarchy + every overlay's per-node and per-edge metadata. This
is the **locked v1 contract** that the Phase 5 interactive web
viewer (#18) and the ``rb hier`` consumer in rtl-buddy both parse.

Pin points (renaming or retyping any of these is a breaking change):

- ``schema_version: "1.0"`` — 1.x payloads are backward-compatible
  by contract; consumers may add new keys freely, never rename or
  retype existing ones.
- ``nodes[].id`` — full instance path, dot-separated from
  ``top``. Reproducible across runs given the same RTL.
- ``nodes[].source.file`` — absolute path; ``decl_line`` /
  ``decl_col`` mark the module declaration's anchor.
- ``nodes[].link`` — ``rtlbuddy://open?file=...&line=...&col=...``
  pointing the hub (or any URI handler) at the node's source.
- ``nodes[].overlays`` — per-overlay metadata, keyed by overlay
  name. Empty when no overlay contributes.
- ``edges[].port_pairs`` — ``[net_expr_at_parent, child_port]``
  pairs; the wave→view joiner (Phase 9) traces signals across the
  hierarchy through these.
- ``overlays_present`` — sorted overlay names whose annotations
  contributed to this view.

The schema file at ``schemas/view-v1.json`` is the authoritative
constraint; the test suite validates every fixture against it.

Everything that can vary across runs is sorted before emission so
two runs over the same input produce identical bytes — keeps the
golden tests and rb-hier-side diffs noise-free.
"""

from __future__ import annotations

import io
import json
from typing import IO
from urllib.parse import quote

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.axi_perf_annotations import AxiPerfMap, Bundle as AxiPerfBundle
from rtl_buddy_view.extractor import Port, PortConnection, SourceLocation
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.reset_annotations import ResetDomainMap

SCHEMA_VERSION = "1.0"


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
    reset_map: ResetDomainMap | None = None,
    axi_perf_map: AxiPerfMap | None = None,
    with_legend: bool = False,
    embed_layout: bool = True,
) -> None:
    """Render ``node`` and its subtree as ``view.json`` v1.

    ``embed_layout`` (default True) bakes the same DOT string the
    ``--format dot`` renderer would emit into a top-level ``layout``
    block. The Phase 5 web viewer prefers this over rebuilding DOT
    in JavaScript so the desktop terminal output and the browser
    schematic share a single layout — clusters, port-rank anchors,
    edge labels, and clock-domain palette all included. Producers
    that don't want the cost (or don't ship Graphviz on the consumer
    side) can pass ``embed_layout=False`` to drop the block.
    """
    payload = _build_payload(
        node,
        domain_map,
        reset_map,
        axi_perf_map,
        with_legend=with_legend,
        embed_layout=embed_layout,
    )
    json.dump(payload, out, indent=2, sort_keys=False)
    out.write("\n")


def _build_payload(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
    *,
    with_legend: bool = False,
    embed_layout: bool = True,
) -> dict:
    nodes = sorted(
        (_node_dict(n, domain_map, reset_map, axi_perf_map) for n in _walk(node)),
        key=lambda d: d["id"],
    )
    edges = sorted(
        _walk_edges(node, domain_map, reset_map, axi_perf_map),
        key=lambda d: (d["from"], d["to"]),
    )
    overlays_present = _overlays_present(domain_map, reset_map, axi_perf_map)
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "top": node.module_name,
        "tool": {"name": "rtl-buddy-view", "version": _version()},
        "nodes": nodes,
        "edges": edges,
        "overlays_present": overlays_present,
    }
    if embed_layout:
        payload["layout"] = _layout_block(
            node,
            domain_map=domain_map,
            reset_map=reset_map,
            with_legend=with_legend,
        )
    return payload


def _layout_block(
    node: HierNode,
    *,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    with_legend: bool,
) -> dict:
    """Build the ``layout`` block by reusing the dot renderer.

    ``domain_map`` / ``reset_map`` / ``with_legend`` are intentionally
    NOT forwarded to the embedded DOT. Producer-baked clock/reset
    fills survive the SPA's overlay toggle (the inline-style clear
    can't reach the polygon's ``fill=`` attribute), and the
    hash-keyed Python palette disagrees with the SPA's first-seen
    assignment so the legend would lie. The SPA is the single source
    of truth for overlay coloring — it reads per-node
    ``overlays.clock`` / ``overlays.reset`` metadata and paints on
    top of a *structurally* laid-out diagram.

    The dot renderer is deterministic given the same inputs, so the
    embedded string is golden-stable across runs — the existing
    determinism contract holds.
    """
    del domain_map, reset_map, with_legend  # see docstring
    buf = io.StringIO()
    # ``as_cluster_tree`` wraps every non-leaf instance in its own
    # cluster subgraph so the SPA's hierarchy view reads as a
    # schematic "block-within-block" diagram instead of a
    # parent-arrow-child tree. The standalone ``--format dot``
    # output keeps the original shape — see ``dot.render``'s
    # docstring for the rationale.
    dot_render.render(node, buf, as_cluster_tree=True)
    # Reverse-map: Graphviz emits the cluster's ``<title>`` as the
    # sanitized DOT identifier (``cluster_<sanitized-instance-path>``),
    # not the instance path itself. The SPA needs the original
    # instance path to wire clicks back to ``nodes[]``; ship the
    # mapping so it doesn't have to re-implement the sanitization.
    cluster_lookup: dict[str, str] = {}
    for n in _walk(node):
        if n.children:
            cluster_lookup[dot_render._cluster_id_for(n.instance_path)] = (
                n.instance_path
            )
    return {
        "engine": "dot",
        "dot": buf.getvalue(),
        "cluster_lookup": cluster_lookup,
    }


def _overlays_present(
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
) -> list[str]:
    """Sorted list of overlay names whose payloads contributed to this view.

    Each map gets its own per-collection check rather than gating on
    the map-level ``is_empty`` — a producer that emitted only
    synchronizers (no flop_resets) still has something to say about
    the reset overlay, and the viewer's toggle should reflect that.
    The clock overlay correspondingly looks at ``clocks`` *or*
    ``flop_domains`` *or* ``crossings`` so a clock-only-crossings
    map surfaces too.
    """
    present: list[str] = []
    if domain_map is not None and (
        domain_map.clocks or domain_map.flop_domains or domain_map.crossings
    ):
        present.append("clock")
    if reset_map is not None and (
        reset_map.flop_resets
        or reset_map.reset_synchronizers
        or reset_map.reset_crossings
        or reset_map.reset_sources
    ):
        present.append("reset")
    if axi_perf_map is not None and (
        axi_perf_map.bundles or axi_perf_map.interconnects
    ):
        present.append("axi-perf")
    return sorted(present)


def _version() -> str:
    """Project version, late-imported to keep this module light."""
    try:
        from importlib.metadata import version as _v

        return _v("rtl-buddy-view")
    except Exception:  # pragma: no cover - importlib is in stdlib
        return "0.0.0"


# --- nodes ------------------------------------------------------------------


def _node_dict(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
) -> dict:
    """One ``view.json`` v1 node entry.

    Pulls the port list off the node's *own* module (the module
    being instantiated) but the per-port expression + anchor off
    the parent's instantiation site — exactly the join the wave
    overlay needs.
    """
    out: dict = {
        "id": node.instance_path,
        "module": node.module_name,
        "instance_name": node.instance.name if node.instance is not None else None,
        "is_blackbox": node.is_blackbox,
        "parameters": _parameters_dict(node),
        "ports": _ports_for_node(node),
        "source": _source_block(node),
        "link": _link_for(node),
        "overlays": _node_overlays(node, domain_map, reset_map, axi_perf_map),
    }
    return out


def _parameters_dict(node: HierNode) -> dict[str, str]:
    """Parameter overrides as ``{name: value_text}``.

    Positional overrides (``param_name is None``) are skipped here —
    callers needing the position-indexed form should consult the
    extractor model directly. v1 stays focused on the named form
    because that's what the viewer's NodeDetail panel surfaces.
    """
    if node.instance is None:
        return {}
    return {
        ov.param_name: ov.value_text
        for ov in node.instance.param_overrides
        if ov.param_name is not None
    }


def _ports_for_node(node: HierNode) -> list[dict]:
    """Render the node's port list with expr/anchor joined from the parent.

    For a non-blackbox child instance, every port the *module*
    declares appears here; ``expr`` and ``anchor`` come from the
    parent-side connection that bound the port (if any).
    Unconnected ports surface with ``expr: null``.

    For blackbox children (module never resolved) we don't know the
    port list, but the *parent's* port_connections tells us which
    port names the parent thought it was connecting to — emit those
    so the viewer can still show the binding.

    For the top node we don't have a parent; ports come from the
    module declaration with ``expr: null``.
    """
    expr_by_name = _expr_by_port(node)
    if node.module is not None and node.module.ports:
        return [_port_dict_from_module(p, expr_by_name) for p in node.module.ports]
    if node.is_blackbox and node.instance is not None:
        # Best-effort: render the named bindings from the parent
        # side; positional bindings are skipped here because v1's
        # ``port.name`` is required-string. The full sequence
        # (including positionals) is still available on the edge's
        # ``port_pairs`` array.
        return [
            _port_dict_from_connection(conn)
            for conn in node.instance.port_connections
            if conn.port_name is not None
        ]
    return []


def _expr_by_port(node: HierNode) -> dict[str, PortConnection]:
    """Map port_name → PortConnection for this node's parent-side bindings.

    Positional connections (port_name is None) are kept under a
    pseudo-name ``$pos$<index>`` so multiple positional bindings
    don't collide; the JSON emit path filters those out (port
    declarations on the module side always carry real names, and
    positional connections without a matching declared port are
    blackbox-only territory).
    """
    by_name: dict[str, PortConnection] = {}
    if node.instance is None:
        return by_name
    pos_idx = 0
    for conn in node.instance.port_connections:
        if conn.port_name is None:
            by_name[f"$pos${pos_idx}"] = conn
            pos_idx += 1
        else:
            by_name[conn.port_name] = conn
    return by_name


def _port_dict_from_module(port: Port, expr_by_name: dict[str, PortConnection]) -> dict:
    conn = expr_by_name.get(port.name)
    return {
        "name": port.name,
        "dir": port.direction,
        "expr": conn.net_expr_text if conn is not None else None,
        "anchor": _anchor_dict(conn.location) if conn is not None else None,
    }


def _port_dict_from_connection(conn: PortConnection) -> dict:
    return {
        "name": conn.port_name,
        "dir": None,
        "expr": conn.net_expr_text,
        "anchor": _anchor_dict(conn.location),
    }


def _anchor_dict(loc: SourceLocation | None) -> dict | None:
    if loc is None:
        return None
    return {"line": loc.start_line, "col": loc.start_column}


def _source_block(node: HierNode) -> dict | None:
    """``source`` block — file path + line range + declaration anchor.

    Prefers the instance's location (where this child is instantiated
    in its parent) since that's the more actionable anchor for the
    "jump to this instance" link; falls back to the module
    declaration when there's no instance (root node).

    The ``decl_line`` / ``decl_col`` fields point specifically at
    the *module* declaration so the viewer can offer two distinct
    jumps: "open this instance" (the instance file/line) vs "open
    the module definition" (decl_*).
    """
    if node.instance is not None and node.instance.location is not None:
        loc = node.instance.location
    elif node.module is not None and node.module.location is not None:
        loc = node.module.location
    else:
        return None
    decl_line = None
    decl_col = None
    if node.module is not None and node.module.location is not None:
        decl_line = node.module.location.start_line
        decl_col = node.module.location.start_column
    return {
        "file": loc.file,
        "start_line": loc.start_line,
        "start_column": loc.start_column,
        "end_line": loc.end_line,
        "end_column": loc.end_column,
        "decl_line": decl_line,
        "decl_col": decl_col,
    }


def _link_for(node: HierNode) -> str | None:
    """``rtlbuddy://open?file=...&line=...&col=...`` for this node.

    Returns None when no source location is known — only happens for
    blackbox nodes the extractor never saw. Real instances always
    carry an ``Instance.location`` and the link surfaces.
    """
    block = _source_block(node)
    if block is None or block.get("file") is None:
        return None
    line = block.get("start_line")
    col = block.get("start_column")
    parts = [f"file={quote(str(block['file']), safe='/:')}"]
    if line is not None:
        parts.append(f"line={line}")
    if col is not None:
        parts.append(f"col={col}")
    return "rtlbuddy://open?" + "&".join(parts)


def _node_overlays(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
) -> dict:
    """Per-overlay contributions for ``node``.

    Each contributor checks the specific data slice it cares about
    rather than the map-level ``is_empty`` — same per-collection
    gating as the tree/dot/mermaid renderers, so sparse producer
    outputs (or synthetic test maps with only one section
    populated) still surface the fields that *are* present.

    Keys absent in the returned dict mean the overlay didn't
    contribute *for this node*; never an error.
    """
    overlays: dict[str, dict] = {}
    if domain_map is not None:
        clock_block = _clock_node_contribution(node, domain_map)
        if clock_block:
            overlays["clock"] = clock_block
    if reset_map is not None:
        reset_block = _reset_node_contribution(node, reset_map)
        if reset_block:
            overlays["reset"] = reset_block
    if axi_perf_map is not None:
        axi_perf_block = _axi_perf_node_contribution(node, axi_perf_map)
        if axi_perf_block:
            overlays["axi-perf"] = axi_perf_block
    return overlays


def _axi_perf_node_contribution(node: HierNode, axi_perf_map: AxiPerfMap) -> dict:
    """Surface the interconnect roll-up when ``node`` is one of the
    producer's named interconnect nodes."""
    ic = axi_perf_map.interconnect_at(node.instance_path)
    if ic is None:
        return {}
    return {
        "interconnect": {
            "total_read_bps": ic.total_read_bps,
            "total_write_bps": ic.total_write_bps,
            "hottest_master": ic.hottest_master,
            "hottest_slave": ic.hottest_slave,
            "arbitration": {
                "fairness_jain": ic.arbitration.fairness_jain,
                "starved_masters": list(ic.arbitration.starved_masters),
            },
        }
    }


def _clock_node_contribution(node: HierNode, domain_map: DomainMap) -> dict:
    """The ``clock`` overlay's per-node contribution.

    For a flop with an explicit ``flop_domains`` entry, surface that
    clock directly — it's the precise per-flop binding. For a
    container/module, fall back to ``predominant_clock`` so the
    viewer can colour subtrees by their dominant domain.
    """
    flop = next(
        (f for f in domain_map.flop_domains if f.instance_path == node.instance_path),
        None,
    )
    if flop is not None and flop.clock is not None:
        return {"clock": flop.clock}
    predominant = domain_map.predominant_clock(node.instance_path)
    if predominant is not None:
        return {"clock": predominant}
    return {}


def _reset_node_contribution(node: HierNode, reset_map: ResetDomainMap) -> dict:
    """The ``reset`` overlay's per-node contribution."""
    out: dict = {}
    flop = reset_map.flop_reset(node.instance_path)
    if flop is not None:
        out["reset"] = flop.reset
        out["polarity"] = flop.polarity
        out["type"] = flop.type
    if node.instance_path in reset_map.synchronizer_paths():
        out["is_synchronizer"] = True
    return out


# --- edges -------------------------------------------------------------------


def _walk_edges(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
):
    for child in node.children:
        yield {
            "from": node.instance_path,
            "to": child.instance_path,
            "port_pairs": _port_pairs(child),
            "overlays": _edge_overlays(
                node, child, domain_map, reset_map, axi_perf_map
            ),
        }
        yield from _walk_edges(child, domain_map, reset_map, axi_perf_map)


def _port_pairs(child: HierNode) -> list[list[str | None]]:
    """``[net_expr_at_parent, child_port_name]`` pairs.

    Wave overlay (Phase 9) traces signals across hierarchy levels
    through these. Positional connections fall back to a ``None``
    child-port slot — the consumer can still see the net but loses
    the port-name binding.
    """
    if child.instance is None:
        return []
    return [
        [conn.net_expr_text, conn.port_name] for conn in child.instance.port_connections
    ]


def _edge_overlays(
    parent: HierNode,
    child: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
) -> dict:
    overlays: dict[str, dict] = {}
    if domain_map is not None:
        clock_block = _clock_edge_contribution(child, domain_map)
        if clock_block:
            overlays["clock"] = clock_block
    if reset_map is not None and reset_map.crossings_into(child.instance_path):
        overlays["reset"] = {"crossing": True}
    if axi_perf_map is not None:
        bundle = axi_perf_map.bundle_at_edge(parent.instance_path, child.instance_path)
        if bundle is not None:
            overlays["axi-perf"] = _axi_perf_bundle_block(bundle)
    return overlays


def _clock_edge_contribution(child: HierNode, domain_map: DomainMap) -> dict:
    """Edge-level clock overlay payload.

    Carries the boolean ``crossing`` flag and, when one or more
    crossings target this edge's destination, the deduplicated
    ``(src_clock, dst_clock)`` pairs with per-pair flop counts. The
    SPA's EdgeDetail panel and edge-label rendering both consume
    this so users see *which* clocks cross without re-opening the
    domain map JSON.

    Multiple flops in the same destination instance frequently share
    a single (src, dst) clock pair; dedup keeps the panel readable.
    """
    crossings = domain_map.crossings_into(child.instance_path)
    if not crossings:
        return {}
    pairs: dict[tuple[str, str], int] = {}
    for c in crossings:
        key = (c.src_clock, c.dst_clock)
        pairs[key] = pairs.get(key, 0) + 1
    return {
        "crossing": True,
        "pairs": [
            {"src_clock": src, "dst_clock": dst, "flops": n}
            for (src, dst), n in sorted(pairs.items())
        ],
    }


def _axi_perf_bundle_block(bundle: AxiPerfBundle) -> dict:
    """Serialize an AxiPerfBundle into the v1 view.json edge-overlay shape."""
    return {
        "name": bundle.name,
        "protocol": bundle.protocol,
        "data_width": bundle.data_width,
        "id_width": bundle.id_width,
        "default_view": bundle.default_view,
        "channels": {
            role: {
                "util_pct": stats.util_pct,
                "bp_pct": stats.bp_pct,
                "peak_occ": stats.peak_occ,
                **({"txns": stats.txns} if stats.txns is not None else {}),
                **({"beats": stats.beats} if stats.beats is not None else {}),
            }
            for role, stats in bundle.channels.items()
        },
        "throughput": {
            "read_bps": bundle.throughput.read_bps,
            "write_bps": bundle.throughput.write_bps,
        },
        "outstanding": {
            "read_peak": bundle.outstanding.read_peak,
            "read_avg": bundle.outstanding.read_avg,
            "write_peak": bundle.outstanding.write_peak,
            "write_avg": bundle.outstanding.write_avg,
        },
        "latency_cycles": {
            "ar_to_r_first": _latency_block(bundle.ar_to_r_first),
            "aw_to_b": _latency_block(bundle.aw_to_b),
        },
        "errors": {
            "slverr": bundle.errors.slverr,
            "decerr": bundle.errors.decerr,
        },
    }


def _latency_block(lat) -> dict:
    return {
        "p50": lat.p50,
        "p95": lat.p95,
        "p99": lat.p99,
        "max": lat.max,
        "hist_log2": list(lat.hist_log2),
    }


# --- traversal --------------------------------------------------------------


def _walk(node: HierNode):
    yield node
    for child in node.children:
        yield from _walk(child)
