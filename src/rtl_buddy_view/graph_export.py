"""Design-tier knowledge-graph export — ``graph.json`` v1 (#126).

``view.json`` is a *render snapshot*: one node per elaborated
instance, shaped for the SPA and the hub resolver. It is not a query
index — modules, ports, parameters, and interfaces are all folded
into per-instance blobs, so "which instances drive port ``clk`` of
``fifo``" costs a full re-walk.

``graph.json`` is the other half: a node-link knowledge graph whose
*vocabulary* is the design's own (module / instance / port /
parameter / interface / modport) and whose edges are typed
relations. It is a durable, mergeable artifact — the design tier of
the cross-repo graph contract shared with rtl_buddy's config tier
(rtl_buddy#376) and binding tier (rtl_buddy#378). Merging is a
node-id union: identical ids across tiers are the deliberate stitch
points (``module:<name>`` is where a config-tier ``maps_to`` edge
lands).

Envelope: NetworkX node-link JSON (``directed``/``multigraph``/
``graph``/``nodes``/``links``), which is what ``graphify
merge-graphs`` and ``graphify query`` accept, and what
``networkx.node_link_graph(data, edges="links")`` round-trips.

Pin points — renaming or retyping any of these is a downstream-
breaking change (see :data:`GRAPH_CONTRACT`, ``docs/graph-json-v1.md``
and ``schemas/graph-v1.json``):

- ``graph.schema_version`` — integer ``1``.
- ``graph.generator.{tool,version,tier}`` — provenance; ``tier`` is
  ``"design"`` for everything this module emits.
- node ids: ``module:<name>``, ``inst:<top>/<instance path>``,
  ``port:<module>.<port>``, ``param:<module>.<name>``,
  ``iface:<name>``, ``modport:<iface>.<name>``.
- every node carries ``id`` / ``type`` / ``label`` / ``tier`` and
  (nullable) ``file`` / ``line``.
- every link carries ``source`` / ``target`` / ``type`` /
  ``confidence``. This module is a deterministic extractor, so
  ``confidence`` is always ``"EXTRACTED"``.

Volatile data (test status, seeds, artefact paths) never lands
here — that is the results overlay, a separate file keyed by node
id. ``graph.json`` changes only when the RTL changes, which is what
makes the content-addressed CST cache enough for incremental
re-export.

Everything that can vary across runs is sorted before emission, so
two runs over the same sources produce identical bytes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

from rtl_buddy_view import query
from rtl_buddy_view.extractor import (
    Instance,
    Interface,
    Module,
    ModuleTable,
    Port,
    SourceLocation,
)
from rtl_buddy_view.graph import HierNode

SCHEMA_VERSION = 1
META_SCHEMA_VERSION = 1
TOOL_NAME = "rtl-buddy-view"
TIER = "design"

#: Only value this tier ever emits — every relation below comes from
#: a deterministic walk of the CST. INFERRED / AMBIGUOUS are reserved
#: for heuristic producers (the cocotb ``dut.<x>`` scan in the
#: binding tier).
CONFIDENCE_EXTRACTED = "EXTRACTED"

NODE_TYPES: tuple[str, ...] = (
    "module",
    "instance",
    "port",
    "parameter",
    "interface",
    "modport",
)
EDGE_TYPES: tuple[str, ...] = (
    "instantiates",
    "child_of",
    "instance_of",
    "connects",
    "implements",
    "overrides",
)

#: The pinned public contract, checked by
#: ``tests/test_graph_export.py::test_graph_contract_keys_present_and_typed``.
#: Keys are dotted paths into the envelope; values are the accepted
#: Python types of the decoded JSON. Adding a key here is a
#: promise to downstream consumers — removing or retyping one is a
#: breaking change.
GRAPH_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "directed": bool,
    "multigraph": bool,
    "graph": dict,
    "graph.schema_version": int,
    "graph.generator": dict,
    "graph.generator.tool": str,
    "graph.generator.version": str,
    "graph.generator.tier": str,
    "graph.project_root_rel": str,
    "graph.design": dict,
    "graph.design.top": str,
    "graph.design.dut_top": (str, type(None)),
    "graph.design.tb_top": (str, type(None)),
    "graph.design.frontend": (str, type(None)),
    "nodes": list,
    "links": list,
}

#: Per-node keys every node carries, whatever its ``type``.
NODE_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "id": str,
    "type": str,
    "label": str,
    "tier": str,
    "file": (str, type(None)),
    "line": (int, type(None)),
}

#: Per-link keys every link carries, whatever its ``type``.
LINK_CONTRACT: dict[str, type | tuple[type, ...]] = {
    "source": str,
    "target": str,
    "type": str,
    "confidence": str,
}


# --- node ids ---------------------------------------------------------------
#
# Stable, human-readable, and prefixed by kind so a sort by id groups
# the graph by node type. The prefix is also what makes cross-tier
# merging safe: a config-tier `test:` id can never collide with a
# design-tier `module:` id.


def module_id(name: str) -> str:
    return f"module:{name}"


def instance_id(top: str, instance_path: str) -> str:
    """``inst:<top>/<instance path>``.

    ``instance_path`` is the hub resolver's instance-path identity —
    dot-separated *including* the top segment, exactly the string
    ``view.json`` uses as ``nodes[].id``. The ``<top>/`` prefix scopes
    the export: a DUT-rooted and a TB-rooted graph of the same design
    merge without their instance nodes colliding.
    """
    return f"inst:{top}/{instance_path}"


def port_id(owner: str, port_name: str) -> str:
    return f"port:{owner}.{port_name}"


def param_id(owner: str, param_name: str) -> str:
    return f"param:{owner}.{param_name}"


def interface_id(name: str) -> str:
    return f"iface:{name}"


def modport_id(interface_name: str, modport_name: str) -> str:
    return f"modport:{interface_name}.{modport_name}"


# --- public API -------------------------------------------------------------


def render(
    node: HierNode,
    out: IO[str],
    *,
    module_table: ModuleTable,
    project_root: Path | None = None,
    project_root_rel: str = ".",
    dut_top: str | None = None,
    tb_top: str | None = None,
    frontend: str | None = None,
    tool_version: str | None = None,
    indent: int = 2,
) -> None:
    """Write ``node``'s design graph to ``out`` as ``graph.json`` v1."""
    payload = build_graph(
        node,
        module_table,
        project_root=project_root,
        project_root_rel=project_root_rel,
        dut_top=dut_top,
        tb_top=tb_top,
        frontend=frontend,
        tool_version=tool_version,
    )
    json.dump(payload, out, indent=indent)
    out.write("\n")


def build_graph(
    root: HierNode,
    table: ModuleTable,
    *,
    project_root: Path | None = None,
    project_root_rel: str = ".",
    dut_top: str | None = None,
    tb_top: str | None = None,
    frontend: str | None = None,
    tool_version: str | None = None,
) -> dict[str, Any]:
    """Build the design-tier node-link payload for ``root``.

    Pure function: no I/O, no clock reading, nothing that can differ
    between two runs over the same ``ModuleTable``.

    Scope is *what was elaborated*: only modules reachable from
    ``root`` get nodes, so a filelist carrying a library of unused
    modules doesn't bloat the graph. Every instance in the
    ``--format json`` hierarchy has a corresponding ``instance``
    node, and its module has a ``module`` (or ``interface``) node —
    that parity is the acceptance criterion from #126.
    """
    builder = _Builder(root=root, table=table, project_root=project_root)
    builder.run()
    return {
        "directed": True,
        "multigraph": True,
        "graph": {
            "schema_version": SCHEMA_VERSION,
            "generator": {
                "tool": TOOL_NAME,
                "version": tool_version if tool_version is not None else _version(),
                "tier": TIER,
            },
            "project_root_rel": project_root_rel,
            "design": {
                "top": root.module_name,
                "dut_top": dut_top,
                "tb_top": tb_top,
                "frontend": frontend,
            },
        },
        "nodes": builder.sorted_nodes(),
        "links": builder.sorted_links(),
    }


def build_meta(
    payload: dict[str, Any],
    files: Iterable[Path],
    *,
    project_root: Path | None = None,
    hasher: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Provenance sidecar for ``graph.json`` (``graph-meta.json``).

    Carries the input content hashes and per-tier generator
    provenance the contract asks for, kept *out* of ``graph.json``
    itself so a re-export that changes nothing structural produces
    byte-identical graph bytes.

    Reads the input files to hash them — the only I/O in this
    module, and injectable via ``hasher`` for tests. Defaults to the
    same SHA-256 content hash the CST cache keys on, so a meta file
    whose hashes are unchanged means every cached CST is still
    valid and the export could have been skipped.
    """
    if hasher is None:
        from rtl_buddy_view.cst_cache import content_hash

        hasher = content_hash

    inputs = []
    for path in sorted(files):
        entry: dict[str, Any] = {"path": _rel_path(str(path), project_root)}
        # A filelist entry that vanished between parse and export is
        # worth recording as unhashed rather than crashing an export
        # that already succeeded.
        try:
            entry["sha256"] = hasher(path)
        except OSError:
            entry["sha256"] = None
        try:
            entry["bytes"] = path.stat().st_size
        except OSError:
            entry["bytes"] = None
        inputs.append(entry)

    graph_block = payload.get("graph", {})
    return {
        "schema_version": META_SCHEMA_VERSION,
        "tiers": {
            TIER: {
                "generator": graph_block.get("generator", {}),
                "design": graph_block.get("design", {}),
                "inputs": inputs,
                "node_count": len(payload.get("nodes", [])),
                "link_count": len(payload.get("links", [])),
            }
        },
    }


# --- builder ----------------------------------------------------------------


class _Builder:
    """Accumulates nodes + links for one export.

    Mutable by design (the AGENTS.md "frozen dataclasses by default"
    rule carves out parser-built collections; this is the same
    shape) but scoped to a single :func:`build_graph` call, so the
    public surface stays a pure function.
    """

    def __init__(
        self,
        *,
        root: HierNode,
        table: ModuleTable,
        project_root: Path | None,
    ) -> None:
        self.root = root
        self.table = table
        self.project_root = project_root
        self.top = root.module_name
        self.nodes: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, Any]] = []

    # -- emission helpers --

    def _add_node(
        self,
        node_id: str,
        *,
        node_type: str,
        label: str,
        location: SourceLocation | None = None,
        **attrs: Any,
    ) -> None:
        """Insert a node, first writer wins.

        Re-emission is normal (a module reached through two instances,
        an interface referenced by two ports); the first emission is
        always the richest because resolved definitions are walked
        before the blackbox stubs that reference them.
        """
        if node_id in self.nodes:
            return
        payload: dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "label": label,
            "tier": TIER,
            "file": _rel_path(location.file, self.project_root) if location else None,
            "line": location.start_line if location else None,
        }
        payload.update(attrs)
        self.nodes[node_id] = payload

    def _add_link(
        self, source: str, target: str, *, link_type: str, **attrs: Any
    ) -> None:
        link: dict[str, Any] = {
            "source": source,
            "target": target,
            "type": link_type,
            "confidence": CONFIDENCE_EXTRACTED,
        }
        link.update(attrs)
        self.links.append(link)

    def sorted_nodes(self) -> list[dict[str, Any]]:
        return [self.nodes[k] for k in sorted(self.nodes)]

    def sorted_links(self) -> list[dict[str, Any]]:
        # Full-payload tie-break: two links can share
        # (source, target, type) — a module instantiating the same
        # child twice, an instance overriding two parameters — and the
        # multigraph envelope keeps both. Sorting on the serialized
        # payload makes the order total, hence the bytes stable.
        return sorted(
            self.links,
            key=lambda link: (
                link["source"],
                link["target"],
                link["type"],
                json.dumps(link, sort_keys=True),
            ),
        )

    # -- passes --

    def run(self) -> None:
        hier_nodes = list(query.walk(self.root))
        self._emit_definitions(hier_nodes)
        self._emit_instances(hier_nodes)

    def _emit_definitions(self, hier_nodes: list[HierNode]) -> None:
        """Module / interface / port / parameter / modport nodes.

        Driven by the set of module names the elaboration actually
        reached. Names the table can't resolve become blackbox
        module nodes whose ports and parameters are recovered from
        the *use* sites — a blackbox's ``.clk(...)`` connection still
        needs a ``port:`` node to point at, or the ``connects`` edge
        (and the binding tier's ``drives`` edge onto it) would be
        dropped.
        """
        observed = _observed_signature(hier_nodes)
        for name in sorted({n.module_name for n in hier_nodes}):
            module = self.table.modules_by_name.get(name)
            if module is not None:
                self._emit_module(module)
            elif name in self.table.interfaces_by_name:
                self._ensure_interface(name)
            else:
                self._emit_blackbox_module(name, observed.get(name, _Signature()))

    def _emit_module(self, module: Module) -> None:
        self._add_node(
            module_id(module.name),
            node_type="module",
            label=module.name,
            location=module.location,
            is_blackbox=False,
            doc=module.leading_doc or None,
        )
        for port in module.ports:
            self._emit_port(module.name, port)
            self._emit_implements(module.name, port)
        for param in module.parameters:
            self._add_node(
                param_id(module.name, param.name),
                node_type="parameter",
                label=param.name,
                location=param.location,
                owner=module.name,
                default=param.default_text,
            )
        for inst in module.instances:
            target = self._definition_id(inst.module_name)
            self._add_link(
                module_id(module.name),
                target,
                link_type="instantiates",
                instance=inst.name,
                file=_rel_path(inst.location.file, self.project_root)
                if inst.location
                else None,
                line=inst.location.start_line if inst.location else None,
            )

    def _emit_port(self, owner: str, port: Port) -> None:
        self._add_node(
            port_id(owner, port.name),
            node_type="port",
            label=port.name,
            location=port.location,
            owner=owner,
            dir=port.direction,
            width=_width_of(port.type_text),
            type_text=port.type_text,
            port_kind=port.port_kind,
            interface_type=port.interface_type,
            modport=port.modport,
        )

    def _emit_implements(self, owner: str, port: Port) -> None:
        """``implements`` edge for a SystemVerilog interface port.

        ``test_mem_if.sub m`` on module ``m3`` means: ``m3``
        implements the ``sub`` view of ``test_mem_if``. A bare
        interface port (no ``.modport`` suffix) pins nothing, so it
        gets no edge.
        """
        if port.port_kind != "interface" or not port.interface_type or not port.modport:
            return
        self._ensure_interface(port.interface_type)
        self._ensure_modport_stub(port.interface_type, port.modport)
        self._add_link(
            module_id(owner),
            modport_id(port.interface_type, port.modport),
            link_type="implements",
            port=port.name,
        )

    def _emit_interface(self, iface: Interface) -> None:
        self._add_node(
            interface_id(iface.name),
            node_type="interface",
            label=iface.name,
            location=iface.location,
            is_blackbox=False,
            signals=[s.name for s in iface.signals],
        )
        for param in iface.parameters:
            self._add_node(
                param_id(iface.name, param.name),
                node_type="parameter",
                label=param.name,
                location=param.location,
                owner=iface.name,
                default=param.default_text,
            )
        for modport in iface.modports:
            self._add_node(
                modport_id(iface.name, modport.name),
                node_type="modport",
                label=modport.name,
                location=modport.location,
                interface=iface.name,
                is_blackbox=False,
                inputs=list(modport.inputs),
                outputs=list(modport.outputs),
                inouts=list(modport.inouts),
            )

    def _emit_blackbox_module(self, name: str, signature: _Signature) -> None:
        self._add_node(
            module_id(name),
            node_type="module",
            label=name,
            location=None,
            is_blackbox=True,
            doc=None,
        )
        for port_name in sorted(signature.ports):
            self._add_node(
                port_id(name, port_name),
                node_type="port",
                label=port_name,
                location=None,
                owner=name,
                dir=None,
                width=None,
                type_text=None,
                port_kind="wire",
                interface_type=None,
                modport=None,
            )
        for param_name in sorted(signature.params):
            self._add_node(
                param_id(name, param_name),
                node_type="parameter",
                label=param_name,
                location=None,
                owner=name,
                default=None,
            )

    def _ensure_interface(self, name: str) -> None:
        """Interface node for ``name``, definition-first.

        Prefers the declaration in the module table (signals,
        parameters, modports and all). Falls back to a blackbox stub
        for a type we never saw declared: the bundle is still a real
        design entity — the port names it, the modport pins
        directions — so it gets a node rather than leaving
        ``implements`` edges dangling.

        Routing *both* callers (an interface instantiated in the
        hierarchy, an interface named by a port) through here is what
        makes the result order-independent: whichever is walked first,
        the real definition wins over the stub.
        """
        if interface_id(name) in self.nodes:
            return
        iface = self.table.interfaces_by_name.get(name)
        if iface is not None:
            self._emit_interface(iface)
            return
        self._add_node(
            interface_id(name),
            node_type="interface",
            label=name,
            location=None,
            is_blackbox=True,
            signals=[],
        )

    def _ensure_modport_stub(self, iface_name: str, modport_name: str) -> None:
        self._add_node(
            modport_id(iface_name, modport_name),
            node_type="modport",
            label=modport_name,
            location=None,
            interface=iface_name,
            is_blackbox=True,
            inputs=[],
            outputs=[],
            inouts=[],
        )

    def _definition_id(self, module_name: str) -> str:
        """``module:`` or ``iface:`` id for a instantiated name.

        SystemVerilog instantiates interfaces with module-instance
        syntax (``test_mem_if u_if();``), so the extractor hands us
        an :class:`Instance` either way. Pointing at the interface
        node keeps ``iface:`` the single identity for the bundle
        instead of forking a bogus ``module:test_mem_if``.
        """
        if (
            module_name not in self.table.modules_by_name
            and module_name in self.table.interfaces_by_name
        ):
            return interface_id(module_name)
        return module_id(module_name)

    def _emit_instances(self, hier_nodes: list[HierNode]) -> None:
        parent_of: dict[str, str] = {}
        for hier in hier_nodes:
            for child in hier.children:
                parent_of[child.instance_path] = hier.instance_path

        for hier in hier_nodes:
            path = hier.instance_path
            node_id = instance_id(self.top, path)
            instance = hier.instance
            is_interface = (
                hier.module_name not in self.table.modules_by_name
                and hier.module_name in self.table.interfaces_by_name
            )
            self._add_node(
                node_id,
                node_type="instance",
                label=instance.name if instance is not None else hier.module_name,
                # The instantiation site is the actionable anchor
                # ("jump to this instance"); the root has no such site,
                # so it falls back to its module declaration — same
                # precedence view.json's ``source`` block uses.
                location=instance.location
                if instance is not None
                else (hier.module.location if hier.module is not None else None),
                instance_path=path,
                instance_name=instance.name if instance is not None else None,
                module=hier.module_name,
                # Mirrors view.json's ``is_blackbox`` so the two
                # artifacts agree node-for-node. An interface instance
                # trips that flag (interfaces aren't in the module
                # table) without being an unknown box, hence the
                # separate ``is_interface`` discriminator.
                is_blackbox=hier.is_blackbox,
                is_interface=is_interface,
                is_top=instance is None,
                depth=path.count("."),
            )
            self._add_link(
                node_id,
                self._definition_id(hier.module_name),
                link_type="instance_of",
            )
            parent_path = parent_of.get(path)
            if parent_path is not None:
                self._add_link(
                    node_id,
                    instance_id(self.top, parent_path),
                    link_type="child_of",
                )
            if instance is not None:
                self._emit_connects(node_id, hier.module_name, instance)
                self._emit_overrides(node_id, hier.module_name, instance)

    def _emit_connects(
        self, node_id: str, module_name: str, instance: Instance
    ) -> None:
        """``connects`` edges: this instance → the formals it binds.

        The port node belongs to the *instantiated* module, so the
        edge reads "instance u_ff binds counter_ff.clk to net ``clk``".
        Positional connections are resolved through the child
        module's declared port order; when the child is a blackbox
        there is no order to resolve against, so the binding is
        recorded on the instance-side only (no edge) rather than
        inventing a formal name.
        """
        ports = self._declared_ports(module_name)
        for index, conn in enumerate(instance.port_connections):
            formal = conn.port_name
            positional = formal is None
            if positional:
                if ports is None or index >= len(ports):
                    continue
                formal = ports[index].name
            self._add_link(
                node_id,
                port_id(module_name, formal or ""),
                link_type="connects",
                formal=formal,
                actual=conn.net_expr_text,
                positional=positional,
                index=index,
                file=_rel_path(conn.location.file, self.project_root)
                if conn.location
                else None,
                line=conn.location.start_line if conn.location else None,
            )

    def _emit_overrides(
        self, node_id: str, module_name: str, instance: Instance
    ) -> None:
        """``overrides`` edges: this instance → the parameters it sets."""
        params = self._declared_parameters(module_name)
        for index, override in enumerate(instance.param_overrides):
            name = override.param_name
            positional = name is None
            if positional:
                if params is None or index >= len(params):
                    continue
                name = params[index]
            self._add_link(
                node_id,
                param_id(module_name, name or ""),
                link_type="overrides",
                parameter=name,
                value=override.value_text,
                positional=positional,
                index=index,
                file=_rel_path(override.location.file, self.project_root)
                if override.location
                else None,
                line=override.location.start_line if override.location else None,
            )

    def _declared_ports(self, module_name: str) -> tuple[Port, ...] | None:
        module = self.table.modules_by_name.get(module_name)
        return module.ports if module is not None else None

    def _declared_parameters(self, module_name: str) -> list[str] | None:
        module = self.table.modules_by_name.get(module_name)
        if module is not None:
            return [p.name for p in module.parameters]
        iface = self.table.interfaces_by_name.get(module_name)
        if iface is not None:
            return [p.name for p in iface.parameters]
        return None


class _Signature:
    """Ports + parameters of an unresolved module, seen from its uses."""

    def __init__(self) -> None:
        self.ports: set[str] = set()
        self.params: set[str] = set()


def _observed_signature(hier_nodes: Iterable[HierNode]) -> dict[str, _Signature]:
    """Recover blackbox signatures from every instantiation site."""
    observed: dict[str, _Signature] = {}
    for hier in hier_nodes:
        instance = hier.instance
        if instance is None:
            continue
        signature = observed.setdefault(hier.module_name, _Signature())
        for conn in instance.port_connections:
            if conn.port_name:
                signature.ports.add(conn.port_name)
        for override in instance.param_overrides:
            if override.param_name:
                signature.params.add(override.param_name)
    return observed


# --- small helpers ----------------------------------------------------------

_PACKED_RANGE_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")


def _width_of(type_text: str | None) -> int | None:
    """Bit width of a port from its verbatim type slice, when knowable.

    ``logic [7:0]`` → 8, ``logic`` → 1, ``logic [WIDTH-1:0]`` → None.
    Deliberately no expression evaluation: a wrong width is worse
    than a missing one for an agent reading the graph, and the
    parameter nodes + ``overrides`` edges already carry what's
    needed to work the real width out.
    """
    if type_text is None:
        return None
    if "[" not in type_text:
        return 1
    match = _PACKED_RANGE_RE.search(type_text)
    if match is None:
        return None
    msb, lsb = int(match.group(1)), int(match.group(2))
    return abs(msb - lsb) + 1


def _rel_path(path: str, project_root: Path | None) -> str:
    """Project-relative POSIX path, falling back to what we were given.

    Node ``file`` fields are relative to the project root so a
    graph.json committed (or shipped between machines) stays valid;
    ``graph.project_root_rel`` tells consumers where that root is
    relative to the graph file itself. Paths outside the root — a
    vendored IP checkout beside it, say — stay absolute rather than
    growing a ``../../..`` prefix that resolves differently under a
    symlinked checkout.
    """
    if project_root is None:
        return path
    try:
        return Path(path).resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path


def _version() -> str:
    """Installed rtl-buddy-view version, best-effort.

    Same lookup and same fallback as the view.json renderer: an
    editable checkout without metadata must still produce a valid
    graph.
    """
    try:
        from importlib.metadata import version as _v

        return _v("rtl-buddy-view")
    except Exception:  # pragma: no cover - importlib is in stdlib
        return "0.0.0"
