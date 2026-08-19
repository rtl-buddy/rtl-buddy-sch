"""Engine-neutral schematic payload — ``elk.json`` v1 (epic #163, P1).

``--format dot`` hands the whole picture to Graphviz: layout, routing,
labels and identity all arrive as one string, and a consumer that
wants to *interact* with the drawing has to scrape SVG ``<title>``
elements back into node names. This module emits the other half — the
**graph**, with no drawing in it.

The shape is ELK JSON (https://eclipse.dev/elk/documentation.html):
a recursive node tree with ``id`` / ``children`` / ``ports`` /
``edges``, which `elkjs <https://github.com/kieler/elkjs>`_ consumes
verbatim. Everything rtl-buddy knows that ELK does not is namespaced
under a single ``rb`` key per object, so the payload stays a valid ELK
graph: an ELK layout call ignores ``rb`` entirely, and P2's canvas
reads it for the schematic dress (pin names, directions, bus widths,
refdes labels).

Three rules make this an interface rather than a render:

1. **No layout options.** Nothing here says WEST, EAST, FIXED_SIDE,
   ``elk.algorithm``, or a pixel size. Which side an input pin lands
   on, how wide a box is, whether clocks route at all — those are
   presentation decisions owned by the consumer, which is also the
   only party that knows the font it will measure text in.
   ``rb.direction`` carries the *fact* (``"input"``); the side is a
   consequence the consumer draws.
2. **Nothing volatile.** Same rule as ``graph.json``: no test status,
   no seeds, no artefact paths, no timestamps, no absolute filesystem
   paths. The payload changes when the RTL changes, which is what
   makes it cacheable and diffable.
3. **All ports, not just connected ones.** A schematic symbol shows
   its whole pinout — an unconnected ``clk`` pin is information (it
   is why the block never ticks), and a consumer cannot recover a pin
   the exporter dropped. ``rb.connected`` distinguishes the two.

Pin points — renaming or retyping any of these breaks P2 (see
:data:`ELK_CONTRACT` and ``docs/elk-json-v1.md``):

- node ``id`` is the **instance path** (``top.u_fifo``), the same
  identity ``view.json`` uses as ``nodes[].id`` and the hub resolver
  keys on. Cross-artifact joins are id equality, not name matching.
- port ``id`` is ``<instance path>:<port name>``.
- edge ``sources`` / ``targets`` reference a port id when the formal
  pin is unambiguous, and the node id otherwise.
- ``rb`` is the only namespace this module writes into.

Pure function over the in-memory ``ModuleTable`` — no I/O, sorted
output, byte-identical across runs (AGENTS.md § Development rules).
"""

from __future__ import annotations

import json
from typing import IO, Any

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.connectivity import (
    _BLOCK_EXTRA_CLOCK_RE,
    _BLOCK_EXTRA_RESET_RE,
    _CLOCK_NAME_RE,
    _RESET_NAME_RE,
    Endpoint,
    NetEdge,
    port_width,
    scope_connectivity,
)
from rtl_buddy_view.extractor import Instance, Module, ModuleTable
from rtl_buddy_view.graph import HierNode

SCHEMA_VERSION = 1
TOOL_NAME = "rtl-buddy-view"

#: The pinned public contract, checked by
#: ``tests/test_elk_export.py::test_elk_contract_keys_present_and_typed``.
#: Keys are dotted paths into the payload root; values are the
#: accepted Python types of the decoded JSON. Mirrors
#: :data:`rtl_buddy_view.graph_export.GRAPH_CONTRACT` — adding a key
#: is a promise to P2's canvas, removing or retyping one is a
#: breaking change.
ELK_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "children": list,
    "ports": list,
    "edges": list,
    "rb": dict,
    "rb.export": dict,
    "rb.export.schema_version": int,
    "rb.export.generator": dict,
    "rb.export.generator.tool": str,
    "rb.export.generator.version": str,
    "rb.export.design": dict,
    "rb.export.design.top": str,
}

#: Per-node keys every node carries, root and leaf alike. A leaf
#: block still has ``children`` / ``edges`` (empty), so a consumer
#: recurses without type-testing.
NODE_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "children": list,
    "ports": list,
    "edges": list,
    "rb": dict,
}

#: Per-node ``rb`` keys. ``clock`` is always present (``null``
#: without a clock overlay) — the same always-there-nullable shape
#: view.json uses, so a consumer never branches on key existence.
NODE_RB_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "instance_name": (str, type(None)),
    "module_name": str,
    "is_blackbox": bool,
    "param_overrides": list,
    "clock": (str, type(None)),
}

#: Per-port keys.
PORT_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "rb": dict,
}

PORT_RB_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "name": str,
    "direction": (str, type(None)),
    "is_clock": bool,
    "is_reset": bool,
    "width": (int, type(None)),
    "connected": bool,
}

#: Per-edge keys.
EDGE_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "sources": list,
    "targets": list,
    "rb": dict,
}

EDGE_RB_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "nets": list,
    "bits": (int, type(None)),
    "src_pins": list,
    "dst_pins": list,
}


# --- ids --------------------------------------------------------------------


def node_id(instance_path: str) -> str:
    """``<instance path>`` — the hub resolver's instance identity."""
    return instance_path


def port_id(instance_path: str, port_name: str) -> str:
    """``<instance path>:<port name>``.

    The colon is what keeps a port id unambiguous against a node id:
    instance paths are dot-separated, so no node can ever collide
    with a port of another node.
    """
    return f"{instance_path}:{port_name}"


def edge_id(source: str, target: str) -> str:
    """``<source ref>-><target ref>``.

    Unique per scope by construction: ``scope_connectivity`` bundles
    every net between one endpoint pair into a single edge, so no two
    edges of a scope share both ends and a direction.
    """
    return f"{source}->{target}"


# --- public API -------------------------------------------------------------


def render(
    node: HierNode,
    out: IO[str],
    *,
    module_table: ModuleTable,
    domain_map: DomainMap | None = None,
    tool_version: str | None = None,
    indent: int = 2,
) -> None:
    """Write ``node``'s ELK payload to ``out`` as JSON."""
    payload = export_elk(
        node,
        module_table,
        domain_map=domain_map,
        tool_version=tool_version,
    )
    json.dump(payload, out, indent=indent)
    out.write("\n")


def export_elk(
    root: HierNode,
    table: ModuleTable,
    *,
    domain_map: DomainMap | None = None,
    tool_version: str | None = None,
) -> dict[str, Any]:
    """Build the ELK-shaped schematic payload rooted at ``root``.

    The returned dict *is* the ELK root node: it carries the top
    module's own ports (P2 draws them as off-page connector flags),
    its children, and the top scope's dataflow edges. Provenance
    lives under ``rb.export`` on the root only, so every other object
    in the tree is a plain node/port/edge.

    ``domain_map`` follows the renderer graceful-degradation contract
    (AGENTS.md § Adding a renderer): ``None`` or empty means the
    output is identical to the un-annotated case, with every
    ``rb.clock`` null.
    """
    active_map = domain_map if (domain_map and not domain_map.is_empty) else None
    payload = _node(root, table, active_map)
    payload["rb"]["export"] = {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "tool": TOOL_NAME,
            "version": tool_version if tool_version is not None else _version(),
        },
        "design": {"top": root.module_name},
    }
    return payload


# --- nodes ------------------------------------------------------------------


def _node(
    hier: HierNode,
    table: ModuleTable,
    domain_map: DomainMap | None,
) -> dict[str, Any]:
    """One ELK node, recursively.

    Children are built before this scope's edges because edge
    endpoints resolve against the children's *emitted* port ids: an
    edge may only reference a pin that actually exists on the box it
    attaches to, or the consumer gets a dangling reference and ELK
    throws.
    """
    children = [
        _node(child, table, domain_map)
        for child in sorted(hier.children, key=lambda c: c.instance_path)
    ]
    ports = _ports(hier, table)
    port_ids = {port["id"] for port in ports}
    for child in children:
        port_ids.update(port["id"] for port in child["ports"])

    return {
        "id": node_id(hier.instance_path),
        "rb": {
            # NOTE: no ``display_label`` here — ``HierNode`` has no
            # such attribute on this branch. The ``rbsch:`` pragma
            # work (#162) adds it, and this is where it lands: P2's
            # box title is ``rb.display_label or rb.instance_name``.
            "instance_name": hier.instance.name if hier.instance is not None else None,
            "module_name": hier.module_name,
            "is_blackbox": hier.is_blackbox,
            "param_overrides": _param_overrides(hier, table),
            "clock": _clock_of(hier, domain_map),
        },
        "ports": ports,
        "children": children,
        "edges": _edges(hier, table, port_ids),
    }


def _param_overrides(hier: HierNode, table: ModuleTable) -> list[list[str | None]]:
    """``[[name, value], …]`` in declaration order.

    Source order, not sorted: an override list reads as the
    instantiation wrote it, and it is already deterministic. A
    positional override is resolved against the child's declared
    parameter order when that is knowable, and carries a ``null``
    name when it is not — a made-up name would be worse than an
    admitted gap on a diagram a human reads.
    """
    instance = hier.instance
    if instance is None:
        return []
    declared = _declared_parameters(hier.module_name, table)
    out: list[list[str | None]] = []
    for index, override in enumerate(instance.param_overrides):
        name = override.param_name
        if name is None and declared is not None and index < len(declared):
            name = declared[index]
        out.append([name, override.value_text])
    return out


def _declared_parameters(module_name: str, table: ModuleTable) -> list[str] | None:
    module = table.modules_by_name.get(module_name)
    if module is not None:
        return [p.name for p in module.parameters]
    iface = table.interfaces_by_name.get(module_name)
    if iface is not None:
        return [p.name for p in iface.parameters]
    return None


def _clock_of(hier: HierNode, domain_map: DomainMap | None) -> str | None:
    """Predominant clock of this node, or ``None`` without an overlay.

    Same precedence the JSON renderer uses: an explicit per-flop
    binding wins, otherwise the subtree's predominant clock. P4's
    clock-domain shading tints on this; P1 only has to carry it.
    """
    if domain_map is None:
        return None
    flop = next(
        (f for f in domain_map.flop_domains if f.instance_path == hier.instance_path),
        None,
    )
    if flop is not None and flop.clock is not None:
        return flop.clock
    return domain_map.predominant_clock(hier.instance_path)


# --- ports ------------------------------------------------------------------


def _ports(hier: HierNode, table: ModuleTable) -> list[dict[str, Any]]:
    """Every port of this node's module, connected or not.

    A blackbox has no declaration to read, so its pinout is recovered
    from the binding site — the same fallback ``graph_export`` makes
    for its port stubs. Those pins carry a ``null`` direction and
    width and are sorted by name (there is no declaration order to
    preserve); a declared module keeps its header order, which is the
    pinout a datasheet would print.
    """
    module = hier.module
    connected = _connected_formals(hier.instance, module)
    if module is None:
        return [
            _port_entry(hier.instance_path, name, None, None, connected=True)
            for name in sorted(connected)
        ]
    return [
        _port_entry(
            hier.instance_path,
            port.name,
            port.direction,
            port_width(port.type_text) if port.port_kind == "wire" else None,
            # The root has no binding site: it is the sheet boundary,
            # so its pins are connected to the outside world by
            # definition.
            connected=hier.instance is None or port.name in connected,
        )
        for port in module.ports
    ]


def _port_entry(
    instance_path: str,
    name: str,
    direction: str | None,
    width: int | None,
    *,
    connected: bool,
) -> dict[str, Any]:
    return {
        "id": port_id(instance_path, name),
        "rb": {
            "name": name,
            "direction": direction,
            # Name-shaped: the shared token form PLUS the widened
            # block-diagram patterns. The widened forms are what
            # suppresses ``wclk``/``cclk`` *routing* as clock trees,
            # and the pin flag must agree with that classification —
            # a wire hidden as a clock whose pin renders as data
            # would be incoherent in the schematic view. The widened
            # patterns are false-positive-safe by construction
            # (underscore-free stem for clocks, polarity tail for
            # resets), so a chevron never lands on a domain-suffixed
            # data pin like ``src_sel_cclk``.
            "is_clock": bool(
                _CLOCK_NAME_RE.search(name) or _BLOCK_EXTRA_CLOCK_RE.search(name)
            ),
            "is_reset": bool(
                _RESET_NAME_RE.search(name) or _BLOCK_EXTRA_RESET_RE.search(name)
            ),
            "width": width,
            "connected": connected,
        },
    }


def _connected_formals(instance: Instance | None, module: Module | None) -> set[str]:
    """Formal names bound at ``instance``'s binding site.

    Positional connections are resolved through the declared port
    order — unlike the connectivity analyzer, which can't trust that
    order to invent *dataflow*, marking a pin "connected" is a claim
    cheap enough to make from an index.
    """
    if instance is None:
        return set()
    declared = module.ports if module is not None else None
    formals: set[str] = set()
    for index, conn in enumerate(instance.port_connections):
        if conn.port_name is not None:
            formals.add(conn.port_name)
        elif declared is not None and index < len(declared):
            formals.add(declared[index].name)
    return formals


# --- edges ------------------------------------------------------------------


def _edges(
    hier: HierNode, table: ModuleTable, port_ids: set[str]
) -> list[dict[str, Any]]:
    """This scope's sibling dataflow, as ELK edges.

    Endpoints reference a **port id** when the connectivity analyzer
    resolved the bundle to exactly one formal pin at that end — that
    is the case P2 draws as a wire landing on a named pin. A
    multi-pin bundle (one edge standing for ``wr_en`` *and*
    ``wr_data``) has no single pin to land on, so it references the
    node and the consumer attaches at the box border; the pin names
    are still in ``rb.src_pins`` / ``rb.dst_pins`` for the label.
    """
    module = hier.module
    if module is None or not hier.children:
        return []
    child_paths = {
        child.instance.name: child.instance_path
        for child in hier.children
        if child.instance is not None
    }
    edges: list[dict[str, Any]] = []
    for edge in scope_connectivity(module, table):
        source = _endpoint_ref(
            edge.src, edge.src_pins, hier.instance_path, child_paths, port_ids
        )
        target = _endpoint_ref(
            edge.dst, edge.dst_pins, hier.instance_path, child_paths, port_ids
        )
        if source is None or target is None:
            continue
        edges.append(_edge_entry(edge, source, target))
    return sorted(edges, key=lambda e: e["id"])


def _edge_entry(edge: NetEdge, source: str, target: str) -> dict[str, Any]:
    return {
        "id": edge_id(source, target),
        "sources": [source],
        "targets": [target],
        "rb": {
            "nets": list(edge.nets),
            "bits": edge.bits,
            "src_pins": list(edge.src_pins),
            "dst_pins": list(edge.dst_pins),
        },
    }


def _endpoint_ref(
    endpoint: Endpoint,
    pins: tuple[str, ...],
    scope_path: str,
    child_paths: dict[str, str],
    port_ids: set[str],
) -> str | None:
    """Resolve one connectivity endpoint to a port id or a node id.

    ``None`` when the endpoint has no node in the hierarchy — a
    module the elaboration never built. Emitting the edge anyway
    would hand ELK a reference to nothing.
    """
    kind, name = endpoint
    if kind == "inst":
        path = child_paths.get(name)
        if path is None:
            return None
    else:
        path = scope_path
    if len(pins) == 1:
        candidate = port_id(path, pins[0])
        if candidate in port_ids:
            return candidate
    return node_id(path)


# --- small helpers ----------------------------------------------------------


def _version() -> str:
    """Installed distribution version, best-effort.

    Tries the current distribution name first and the pre-rename one
    second (the same two-step ``__init__`` does), so an install made
    before the ``rtl-buddy-sch`` rename still stamps a real version.
    An editable checkout with no metadata at all must still produce a
    valid payload, hence the ``0.0.0`` floor.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _v

    for dist in ("rtl-buddy-sch", "rtl-buddy-view"):
        try:
            return _v(dist)
        except PackageNotFoundError:
            continue
    return "0.0.0"  # pragma: no cover - source tree without dist metadata
