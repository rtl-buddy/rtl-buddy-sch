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
from pathlib import Path
from typing import IO
from urllib.parse import quote

from rtl_buddy_view import elk_export
from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.axi_perf_annotations import AxiPerfMap, Bundle as AxiPerfBundle
from rtl_buddy_view.coverage_annotations import CoverageMap
from rtl_buddy_view.extractor import (
    ModuleTable,
    Port,
    PortConnection,
    SourceLocation,
    flatten_interface_ports,
)
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.hints import HintMap
from rtl_buddy_view.render import dot as dot_render
from rtl_buddy_view.reset_annotations import ResetDomainMap
from rtl_buddy_view.wave_annotations import WaveMap

SCHEMA_VERSION = "1.1"


def render(
    node: HierNode,
    out: IO[str],
    *,
    domain_map: DomainMap | None = None,
    reset_map: ResetDomainMap | None = None,
    axi_perf_map: AxiPerfMap | None = None,
    wave_map: WaveMap | None = None,
    coverage_map: CoverageMap | None = None,
    coverage_metric: str = "lines",
    axi_perf_source: Path | None = None,
    with_legend: bool = False,
    embed_layout: bool = True,
    module_table: ModuleTable | None = None,
    dut_top: str | None = None,
    tb_top: str | None = None,
    hints: HintMap | None = None,
) -> None:
    """Render ``node`` and its subtree as ``view.json`` v1.

    ``coverage_map`` (Phase 6 — #20) contributes per-node
    ``overlays.coverage`` rollups joined by the defining module's
    source range, plus an ``overlay_meta.coverage`` block carrying
    the source path, Coverview URL base, and the ``--coverage-metric``
    channel the viewer should drive its heatmap tint from.

    ``embed_layout`` (default True) bakes the same DOT string the
    ``--format dot`` renderer would emit into a top-level ``layout``
    block. The Phase 5 web viewer prefers this over rebuilding DOT
    in JavaScript so the desktop terminal output and the browser
    schematic share a single layout — clusters, port-rank anchors,
    edge labels, and clock-domain palette all included. When a
    ``module_table`` is supplied the same block additionally carries
    ``layout.elk``, the engine-neutral schematic payload
    (``docs/elk-json-v1.md``) the SPA's elkjs canvas lays out
    in-browser. Producers that don't want the cost (or don't ship
    Graphviz on the consumer side) can pass ``embed_layout=False``
    to drop the block.

    ``axi_perf_source`` (the original ``--overlay axi-perf=PATH``
    argument) lands as a top-level ``axi_perf`` block carrying the
    absolute source path and — when the path matches the canonical
    ``<suite_dir>/artefacts/axi/<test>/axi-perf.json`` layout —
    derived ``test`` / ``suite_dir`` fields. The SPA's "Open in
    marimo" button (Phase 2 of the marimo umbrella) reads this
    block to skip the test/suite_dir prompts. Absent when no
    axi-perf overlay was supplied.

    ``dut_top`` / ``tb_top`` (v1.1) record the module names supplied
    via ``--top`` and ``--tb-top`` respectively, even when only one
    was passed. They're additive descriptive fields — the rendered
    root is always whatever ``node`` is — so the SPA can draw a DUT
    boundary box at every instance whose ``module == dut_top`` and
    the hub can choose the wave↔view coordinate mapping (identity
    when ``tb_top`` is set, ``tb_prefix`` strip otherwise) without
    re-reading config. ``None`` for both is valid (the field is
    omitted as ``null``) — covers direct ``json_render.render``
    callers that don't know the CLI surface.
    """
    payload = _build_payload(
        node,
        domain_map,
        reset_map,
        axi_perf_map,
        wave_map,
        coverage_map=coverage_map,
        coverage_metric=coverage_metric,
        axi_perf_source=axi_perf_source,
        with_legend=with_legend,
        embed_layout=embed_layout,
        module_table=module_table,
        dut_top=dut_top,
        tb_top=tb_top,
        hints=hints,
    )
    json.dump(payload, out, indent=2, sort_keys=False)
    out.write("\n")


def _build_payload(
    node: HierNode,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
    wave_map: WaveMap | None = None,
    *,
    coverage_map: CoverageMap | None = None,
    coverage_metric: str = "lines",
    axi_perf_source: Path | None = None,
    with_legend: bool = False,
    embed_layout: bool = True,
    module_table: ModuleTable | None = None,
    dut_top: str | None = None,
    tb_top: str | None = None,
    hints: HintMap | None = None,
) -> dict:
    nodes = sorted(
        (
            _node_dict(
                n,
                domain_map,
                reset_map,
                axi_perf_map,
                wave_map,
                module_table,
                coverage_map=coverage_map,
            )
            for n in _walk(node)
        ),
        key=lambda d: d["id"],
    )
    edges = sorted(
        _walk_edges(node, domain_map, reset_map, axi_perf_map),
        key=lambda d: (d["from"], d["to"]),
    )
    overlays_present = _overlays_present(
        domain_map, reset_map, axi_perf_map, wave_map, coverage_map
    )
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "top": node.module_name,
        "dut_top": dut_top,
        "tb_top": tb_top,
        "tool": {"name": "rtl-buddy-view", "version": _version()},
        "nodes": nodes,
        "edges": edges,
        "overlays_present": overlays_present,
    }
    # Top-level overlay_meta surfaces overlay-wide knobs the viewer
    # uses for the legend (e.g. "@ 12500000 fs" annotation next to the
    # wave toggle). Only emit the wave block when a non-empty payload
    # exists; consistent with the graceful-degradation contract the
    # clock + reset overlays follow.
    if wave_map is not None and not wave_map.is_empty:
        payload.setdefault("overlay_meta", {})["wave"] = {
            "t_fs": str(wave_map.t_fs),
            "source_file": wave_map.source_file,
        }
    if coverage_map is not None and not coverage_map.is_empty:
        payload.setdefault("overlay_meta", {})["coverage"] = {
            "source": coverage_map.source,
            "url_base": coverage_map.url_base,
            "metric": coverage_metric,
        }
    if axi_perf_source is not None:
        block = _axi_perf_source_block(axi_perf_source)
        # Carry the clock period so the SPA can compute each bundle's
        # theoretical max throughput (data_width × clock frequency).
        if axi_perf_map is not None and axi_perf_map.clock_period_ns:
            block["clock_period_ns"] = axi_perf_map.clock_period_ns
        payload["axi_perf"] = block
    if embed_layout:
        payload["layout"] = _layout_block(
            node,
            domain_map=domain_map,
            reset_map=reset_map,
            with_legend=with_legend,
            module_table=module_table,
            hints=hints,
        )
    return payload


def _axi_perf_source_block(source: Path) -> dict:
    """Build the top-level ``axi_perf`` metadata block.

    Always emits ``source`` (the absolute file path). Tries to derive
    ``test`` + ``suite_dir`` from the canonical artefact layout
    ``<suite_dir>/artefacts/axi/<test>/axi-perf.json`` — when the
    path doesn't match, those fields are omitted and the SPA falls
    back to its prompt path. Conservative on purpose: a wrong
    derivation would silently feed garbage into the marimo launcher.
    """
    from pathlib import Path as _P

    abs_source = _P(source).resolve()
    block: dict = {"source": str(abs_source)}
    parts = abs_source.parts
    # Walk from the file up looking for ``.../artefacts/axi/<test>/<file>``.
    # The penultimate part is the test name; three parents up is the
    # suite_dir. Anything else → no derivation.
    try:
        # abs_source = .../<suite>/artefacts/axi/<test>/axi-perf.json
        if len(parts) >= 5 and parts[-3] == "axi" and parts[-4] == "artefacts":
            block["test"] = parts[-2]
            block["suite_dir"] = str(abs_source.parent.parent.parent.parent)
    except (IndexError, OSError):
        # Don't let derivation failures break the JSON emit — `source`
        # alone is enough for the SPA to surface the path in the
        # prompt as a hint.
        pass
    return block


def _layout_block(
    node: HierNode,
    *,
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    with_legend: bool,
    module_table: ModuleTable | None = None,
    hints: HintMap | None = None,
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

    A second, **additive** key rides alongside: ``layout.elk`` — the
    engine-neutral schematic payload ``--format elk`` emits
    (``docs/elk-json-v1.md``). It is not in ``JSON_CONTRACT``: this
    is the SPA's schematic canvas talking to its own producer, not
    the ``rb hier`` wire contract, and a consumer that doesn't know
    the key ignores it exactly as the ``additionalProperties: true``
    schema says it may. It needs the ``ModuleTable`` (formal pin
    names and declared widths live there, not on ``HierNode``), so
    a caller that didn't supply one gets the dot-only block it
    always got — degraded, never wrong.

    ``domain_map`` *is* forwarded to the ELK payload, unlike the DOT:
    it lands there as a per-node ``rb.clock`` string, not as a baked
    fill, so nothing the SPA's overlay toggle needs to clear is
    frozen into the picture.
    """
    del reset_map, with_legend  # see docstring
    buf = io.StringIO()
    # ``as_cluster_tree`` wraps every non-leaf instance in its own
    # cluster subgraph so the SPA's hierarchy view reads as a
    # schematic "block-within-block" diagram instead of a
    # parent-arrow-child tree. The standalone ``--format dot``
    # output keeps the original shape — see ``dot.render``'s
    # docstring for the rationale.
    # ``hints`` (#180): the embedded cluster tree honours the layout
    # vocabulary (``rank=`` / ``group=``) exactly like ``--format
    # dot`` does, and the embedded ELK payload gets the same
    # classification / bundle / pin-direction effects as ``--format
    # elk`` — the SPA is a first-class consumer, not a stale copy.
    dot_render.render(
        node, buf, as_cluster_tree=True, module_table=module_table, hints=hints
    )
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
    block: dict = {
        "engine": "dot",
        "dot": buf.getvalue(),
        "cluster_lookup": cluster_lookup,
    }
    if module_table is not None:
        block["elk"] = elk_export.export_elk(
            node,
            module_table,
            domain_map=domain_map,
            tool_version=_version(),
            hints=hints,
        )
    return block


def _overlays_present(
    domain_map: DomainMap | None,
    reset_map: ResetDomainMap | None,
    axi_perf_map: AxiPerfMap | None,
    wave_map: WaveMap | None = None,
    coverage_map: CoverageMap | None = None,
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
    if wave_map is not None and not wave_map.is_empty:
        present.append("wave")
    if coverage_map is not None and not coverage_map.is_empty:
        present.append("coverage")
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
    wave_map: WaveMap | None = None,
    module_table: ModuleTable | None = None,
    *,
    coverage_map: CoverageMap | None = None,
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
        "ports": _ports_for_node(node, module_table),
        "source": _source_block(node),
        "link": _link_for(node),
        "overlays": _node_overlays(
            node,
            domain_map,
            reset_map,
            axi_perf_map,
            wave_map,
            coverage_map=coverage_map,
        ),
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


def _ports_for_node(
    node: HierNode, module_table: ModuleTable | None = None
) -> list[dict]:
    """Render the node's port list with expr/anchor joined from the parent.

    For a non-blackbox child instance, every port the *module*
    declares appears here; ``expr`` and ``anchor`` come from the
    parent-side connection that bound the port (if any).
    Unconnected ports surface with ``expr: null``.

    Interface ports (``port_kind == "interface"``) are flattened into
    per-signal scalar ports via :func:`flatten_interface_ports` when
    the interface body is in scope. Unresolved interface types fall
    back to the bundle row from #102. (#105.)

    For blackbox children (module never resolved) we don't know the
    port list, but the *parent's* port_connections tells us which
    port names the parent thought it was connecting to — emit those
    so the viewer can still show the binding.

    For the top node we don't have a parent; ports come from the
    module declaration with ``expr: null``.
    """
    expr_by_name = _expr_by_port(node)
    if node.module is not None and node.module.ports:
        ports = flatten_interface_ports(node.module, module_table)
        return [_port_dict_from_module(p, expr_by_name) for p in ports]
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
    if conn is None and port.port_kind == "interface_signal":
        # Flattened interface signals (``m.req``) don't have their own
        # parent-side binding — the source-level binding is at the
        # owning interface port (``m``). Look that up so the SPA can
        # still wire connectivity through the interface instance
        # (e.g. block-flow edges anchored at ``_in_u_if.sub``). (#105.)
        owning = port.name.split(".", 1)[0]
        conn = expr_by_name.get(owning)
    out: dict = {
        "name": port.name,
        "dir": port.direction,
        "expr": conn.net_expr_text if conn is not None else None,
        "anchor": _anchor_dict(conn.location) if conn is not None else None,
    }
    # Interface ports (``test_mem_if.sub m``) and their flattened
    # per-signal variants (``m.req``, kind=``interface_signal``) opt
    # into the three extra keys so downstream renderers can style
    # them distinctively. Wire ports — the overwhelming majority —
    # keep the minimal v1 shape. (rtl-buddy-view#102, #105.)
    if port.port_kind in ("interface", "interface_signal"):
        out["port_kind"] = port.port_kind
        out["interface_type"] = port.interface_type
        out["modport"] = port.modport
    return out


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
    wave_map: WaveMap | None = None,
    *,
    coverage_map: CoverageMap | None = None,
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
    if wave_map is not None and not wave_map.is_empty:
        wave_block = _wave_node_contribution(node, wave_map)
        if wave_block:
            overlays["wave"] = wave_block
    if coverage_map is not None and not coverage_map.is_empty:
        coverage_block = _coverage_node_contribution(node, coverage_map)
        if coverage_block:
            overlays["coverage"] = coverage_block
    return overlays


def _coverage_node_contribution(
    node: HierNode, coverage_map: CoverageMap
) -> dict | None:
    """Per-node coverage rollup, joined by the *defining module's*
    source range — not the instance anchor.

    ``node.source`` points at the instantiation site in the parent's
    file, which is the right anchor for "jump to this instance" but
    the wrong one for coverage: LCOV records live in the module
    body's own file. ``node.module.location`` spans exactly that
    body, so every DA/BRDA unit inside lands in this node's bucket.
    Consequence worth naming: all instances of one module share a
    rollup, because merged LCOV has no per-instance resolution.

    Blackboxes (no module) and modules without a recorded location
    contribute nothing — the viewer renders those gray.
    """
    if node.module is None or node.module.location is None:
        return None
    loc = node.module.location
    return coverage_map.rollup(loc.file, loc.start_line, loc.end_line)


def _wave_node_contribution(node: HierNode, wave_map: WaveMap) -> dict:
    """Per-node wave snapshot: ``{t_fs, ports: [{name, value}, ...]}``.

    Walks the node's declared port list and looks each one up in the
    wave-map by hierarchy-suffix matching. Ports the VCD didn't carry
    are silently omitted — the viewer's wave overlay shows ``??`` for
    missing entries (or just no badge), matching the graceful-fallback
    requirement from #21.
    """
    ports = node.module.ports if node.module is not None else ()
    if not ports:
        return {}
    rows: list[dict[str, str]] = []
    for port in ports:
        # Interface ports are bundles, not scalars — a single literal
        # next to ``test_mem_if.sub m`` would be misleading. The SPA's
        # wave overlay also enforces this; skipping here keeps the
        # offline-snapshot contract aligned so an ``rb wave produce``
        # against this design doesn't surface stray bundle entries
        # via accidental signal-name collisions. (rtl-buddy-view#102.)
        if port.port_kind == "interface":
            continue
        value = wave_map.find_for_port(node.instance_path, port.name)
        if value is None:
            continue
        rows.append({"name": port.name, "value": _format_sv_literal(value)})
    if not rows:
        return {}
    # Deterministic order: alphabetical by port name. Matches the
    # SPA's wave.js renderer's sort discipline so screenshots are
    # stable.
    rows.sort(key=lambda r: r["name"])
    return {"t_fs": str(wave_map.t_fs), "ports": rows}


def _format_sv_literal(value: str) -> str:
    """Convert the VCD-reader's raw bit string into an SV literal.

    Scalars stay as-is (a single character: ``0`` / ``1`` / ``x`` /
    ``z``). Multi-bit vectors come back as ``WIDTH'b<bits>`` so the
    viewer-side wave.js renderer can hex-format or pass through
    unchanged. Real / string values (prefix ``r`` / ``s``) pass
    through verbatim — the viewer chooses how to render them.
    """
    if not value:
        return value
    if value[0] in ("r", "s"):
        return value
    if len(value) == 1:
        return f"1'b{value}"
    return f"{len(value)}'b{value}"


def _axi_perf_node_contribution(node: HierNode, axi_perf_map: AxiPerfMap) -> dict:
    """The ``axi-perf`` overlay's per-node contribution.

    Two independent slices, either of which may be present:

    * ``interconnect`` — the producer's roll-up when ``node`` is one of
      the named interconnect nodes.
    * ``bundle_pins`` — one entry per interface port on this node that a
      verible-interface bundle binds to. This is the "tb-top mechanism"
      attach point: the SPA decorates the existing interface-port pin
      with the bundle's perf stats instead of drawing a parallel
      (master, slave) edge. Empty for regex-detected bundles (which
      carry no ``interface_instance``).
    """
    out: dict = {}
    ic = axi_perf_map.interconnect_at(node.instance_path)
    if ic is not None:
        out["interconnect"] = {
            "total_read_bps": ic.total_read_bps,
            "total_write_bps": ic.total_write_bps,
            "hottest_master": ic.hottest_master,
            "hottest_slave": ic.hottest_slave,
            "arbitration": {
                "fairness_jain": ic.arbitration.fairness_jain,
                "starved_masters": list(ic.arbitration.starved_masters),
            },
        }
    pins = _axi_perf_bundle_pins(node, axi_perf_map)
    if pins:
        out["bundle_pins"] = pins
    return out


# Modport-name → endpoint-role mapping. AXI_BUS uses Master/Slave;
# AMBA-5 style uses Manager/Subordinate; some IPs abbreviate. Anything
# unrecognised yields role=None (the pin still renders, just without a
# resolved peer endpoint).
_SLAVE_MODPORTS = frozenset({"slave", "subordinate", "sub", "sbd", "s"})
_MASTER_MODPORTS = frozenset({"master", "manager", "mgr", "mst", "m"})


def _role_for_modport(modport: str | None) -> str | None:
    if modport is None:
        return None
    m = modport.lower()
    if m in _SLAVE_MODPORTS:
        return "slave"
    if m in _MASTER_MODPORTS:
        return "master"
    return None


def _interface_instance_for_port(
    node: HierNode, port: Port, expr_by_name: dict[str, PortConnection]
) -> str | None:
    """Resolve the dotted interface-instance path an interface port binds.

    The interface instance lives in the *parent* scope; the DUT's
    interface port carries ``expr`` like ``"slv.Slave"``, so the
    instance's local name is ``expr.split('.')[0]`` and its absolute
    path is the parent prefix of this node plus that name — e.g. for
    node ``tb_axi_fifo_simple.i_dut`` binding ``slv.Slave`` the bundle
    lives on ``tb_axi_fifo_simple.slv``. Returns None when the port has
    no parent-side binding to derive the instance from.
    """
    conn = expr_by_name.get(port.name)
    if conn is None or not conn.net_expr_text:
        return None
    local = conn.net_expr_text.split(".", 1)[0].strip()
    if not local:
        return None
    parent_prefix = (
        node.instance_path.rsplit(".", 1)[0] if "." in node.instance_path else ""
    )
    return f"{parent_prefix}.{local}" if parent_prefix else local


def _is_ancestor_scope(ancestor: str, descendant: str) -> bool:
    """True when ``ancestor`` is ``descendant`` or one of its parent
    scopes (``a`` == ``b`` or ``b`` starts with ``a.``)."""
    return descendant == ancestor or descendant.startswith(f"{ancestor}.")


def _axi_perf_bundle_pins(node: HierNode, axi_perf_map: AxiPerfMap) -> list[dict]:
    """The AXI bundle pins that decorate ``node``.

    Two resolution passes, both keyed so a bundle attaches to the node
    that actually *owns* its AXI ports:

    1. **Interface pins** — for each ``port_kind == "interface"`` port on
       the node, resolve its interface-instance path and match a
       verible-interface bundle. The pin anchors to the parsed interface
       port (the "tb-top mechanism").
    2. **Manifest-described pins (synthesized)** — for any other bundle
       whose ``master_path`` / ``slave_path`` names this node, synthesize
       a pin from the manifest. This is the escape hatch for designs
       whose AXI ports the CST can't see (macro-generated flat ports):
       the ``axi-bundles.yaml`` *describes* the ports, so the bundle is
       drawn from that description instead of a parsed port. The pin
       attaches to the endpoint node that is **not** an ancestor scope of
       the other endpoint, so a bundle between a procedural-TB scope and
       its DUT lands on the DUT (the real port owner), with the scope as
       the boundary ``peer``.

    Each pin carries ``role`` (master/slave), ``peer`` (the other
    endpoint), and ``synthetic`` so the SPA knows whether to anchor on a
    real interface cell or draw a synthesized bundle pin.
    """
    pins: list[dict] = []
    seen: set[str] = set()

    # Pass 1 — real interface ports.
    if node.module is not None and node.module.ports:
        expr_by_name = _expr_by_port(node)
        for port in node.module.ports:
            if port.port_kind != "interface":
                continue
            iface_inst = _interface_instance_for_port(node, port, expr_by_name)
            if iface_inst is None:
                continue
            bundle = axi_perf_map.bundle_at_interface_instance(iface_inst)
            if bundle is None:
                continue
            role = _role_for_modport(port.modport)
            peer: str | None = None
            if role == "slave":
                peer = bundle.master_path or None
            elif role == "master":
                peer = bundle.slave_path or None
            pins.append(
                {
                    "port": port.name,
                    "modport": port.modport,
                    "interface_instance": iface_inst,
                    "role": role,
                    "peer": peer,
                    "synthetic": False,
                    "bundle": _axi_perf_bundle_block(bundle),
                }
            )
            seen.add(bundle.name)

    # Pass 2 — manifest-described (synthesized) pins by endpoint path.
    path = node.instance_path
    for bundle in axi_perf_map.iter_bundles():
        if bundle.name in seen:
            continue
        role = None
        peer = None
        modport = None
        if path == bundle.slave_path and not _is_ancestor_scope(
            path, bundle.master_path
        ):
            role, peer, modport = "slave", bundle.master_path, bundle.slave_modport
        elif path == bundle.master_path and not _is_ancestor_scope(
            path, bundle.slave_path
        ):
            role, peer, modport = "master", bundle.slave_path, bundle.master_modport
        else:
            continue
        seen.add(bundle.name)
        pins.append(
            {
                "port": bundle.name,
                "modport": modport,
                "interface_instance": bundle.interface_instance or None,
                "role": role,
                "peer": peer or None,
                "synthetic": True,
                "bundle": _axi_perf_bundle_block(bundle),
            }
        )

    # Deterministic order: alphabetical by the pin's port/bundle name.
    pins.sort(key=lambda p: p["port"])
    return pins


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
