"""Scope-local net connectivity — sibling-to-sibling dataflow.

The hierarchy graph (:mod:`rtl_buddy_view.graph`) answers *what
instantiates what*. A block diagram needs the orthogonal question:
*within one module, which child talks to which other child, and over
what net*. That is what this module computes.

Everything here is a pure function over the in-memory
:class:`~rtl_buddy_view.extractor.ModuleTable` — no I/O, no toolchain
dependency, deterministic output (see AGENTS.md § Development rules).

The algorithm is deliberately structural rather than semantic:

1. Every child-instance pin is mapped to the **root identifiers** of
   the expression it is bound to — ``hwif_out.ctrl.SRC.value`` and
   ``hwif_out.op.OP.value`` both reduce to ``hwif_out``, and
   ``cmd_data[18:16]`` reduces to ``cmd_data``.
2. Continuous assignments build a **directed value-flow graph** over
   those roots: ``assign lhs = rhs;`` adds one edge ``rhs_root ->
   lhs_root`` per root pair, because value flows out of the RHS and
   into the LHS. Chains compose, so ``assign c = b; assign b = a;``
   makes ``c`` reachable from ``a`` but not the other way round.
3. Pins with a known ``output`` / ``inout`` direction are drivers and
   ``input`` pins are sinks; the enclosing module's own ports join in
   from the outside (``input`` port = driver, ``output`` / ``inout``
   port = sink). A driver **sources** its roots; a sink **reads**
   its roots.
4. A driver reaches a sink when one of its sourced roots reaches one
   of the sink's read roots under reflexive-transitive closure of the
   flow graph. Those pairs become edges (labelled with the reached
   sink-side roots), which are then bundled per endpoint pair.

Direction matters here, and that is the point: a single assign that
mixes a data source with a status net —
``assign wr_en = hwif_out.push.value & ~wr_full;`` — would, under an
undirected alias model, merge ``wr_full``'s driver into ``hwif_out``'s
group and make the FIFO look like a driver of everything the CSR block
feeds. Directed reachability keeps ``wr_full`` flowing only *into*
``wr_en``.

Remaining limits, all downstream of not running an elaborator:

* Roots are recovered by a textual regex, so a net named inside a
  macro body or built by generate-scope mangling is invisible.
* Index expressions count as reads (``mem[addr]`` yields both), and
  bit ranges are ignored — two disjoint slices of one vector alias.
* Procedural assignments (``always`` blocks) contribute no flow edges;
  only continuous ``assign`` statements do.
* Clock/reset filtering is name-shaped, applied to the **weakly**
  connected components of the flow graph (see
  :func:`_is_clock_or_reset_group`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rtl_buddy_view.extractor import Module, ModuleTable

#: Clock/reset-like names appearing as an underscore-delimited token.
#: Used to drop the clock and reset trees from block diagrams — they
#: fan out to nearly every instance and bury the actual dataflow.
#: ``aclk``-style names are deliberately *not* matched: requiring the
#: token form avoids false positives on legitimate names that merely
#: contain "clk". Defined here (rather than in the dot renderer, which
#: is where they started) so the analyzer and the renderer share one
#: definition; ``render/dot.py`` imports them back under their original
#: names.
_CLOCK_NAME_RE = re.compile(r"(?i)(?:^|_)(clk|clock)(?:$|_)")
_RESET_NAME_RE = re.compile(r"(?i)(?:^|_)(rst|reset)(?:$|_)")

#: Block-diagram-only widening of the clock/reset patterns above.
#: Prefixed-but-tokenless clock names (``cclk``, ``wclk``, ``sysclk``)
#: and polarity-tailed reset names (``crst_n``, ``aresetn``, ``rstn``)
#: don't carry the underscore token the shared regexes require, and a
#: partially-filtered clock tree (``apb_clk`` dropped, ``cclk`` kept)
#: reads worse than either extreme. Both forms anchor on an
#: underscore-free stem: a multi-token net whose *last* token merely
#: ends in "clk" is domain-suffixed data, not a clock
#: (``src_sel_cclk``, ``result_payload_cclk``), and the reset form
#: additionally insists on the ``n``/``nb``/``ni`` polarity tail so
#: data names ending in "rst" (``burst``, ``first``) survive. Only
#: :func:`_is_clock_or_reset_group` consults these — the default
#: (non-block) renderer paths keep the conservative token-form
#: patterns, so their output is unchanged.
_BLOCK_EXTRA_CLOCK_RE = re.compile(r"(?i)^[^_]*(clk|clock)$")
_BLOCK_EXTRA_RESET_RE = re.compile(r"(?i)^[^_]*(rst|reset)_?n[ib]?$")

#: An identifier token that is *not* a field select (not preceded by
#: ``.``), not the base part of a sized literal (not preceded by
#: ``'``), and not a system task/function name (not preceded by ``$``).
#: The lookbehind also excludes identifier characters so the tail of a
#: longer identifier never matches on its own.
_ROOT_IDENT_RE = re.compile(r"(?<![A-Za-z0-9_$.'])[A-Za-z_][A-Za-z0-9_$]*")

#: ``("inst", <instance name>)`` or ``("port", <port name>)`` — one end
#: of a :class:`NetEdge`, relative to the scope being analyzed.
Endpoint = tuple[Literal["inst", "port"], str]

_Direction = Literal["input", "output", "inout"]


@dataclass(frozen=True)
class NetEdge:
    """A directed dataflow edge between two endpoints of one scope.

    ``nets`` carries the representative net names the edge stands for
    — sorted and deduped, so a 19-bit payload split across three sink
    pins collapses to the single net that actually carries it. All
    edges sharing an endpoint pair are bundled into one record.
    """

    src: Endpoint
    dst: Endpoint
    nets: tuple[str, ...]


def root_identifiers(expr_text: str) -> tuple[str, ...]:
    """Return the root identifiers referenced by an expression slice.

    "Root" means the leading name of a reference chain: the thing that
    is actually a net in the enclosing scope. Field selects, bit and
    part selects, and array indices all reduce to it::

        hwif_out.ctrl.SRC.value  ->  ("hwif_out",)
        cmd_dst_data_cclk[18:16] ->  ("cmd_dst_data_cclk",)
        {a, b[3:0], c.d}         ->  ("a", "b", "c")
        2'b00                    ->  ()

    Sized-literal bases (the ``b00`` of ``2'b00``) and system
    task/function names (the ``clog2`` of ``$clog2(W)``) are excluded.
    Names appearing purely as index expressions are *not* excluded —
    ``mem[addr]`` yields both ``mem`` and ``addr``. That over-match is
    intentional: an index is a real read of that net, and pin
    directions downstream keep the resulting edges honest.

    The result is deduped, in first-appearance order, so callers get
    deterministic output without needing to sort.
    """
    seen: dict[str, None] = {}
    for match in _ROOT_IDENT_RE.finditer(expr_text):
        seen.setdefault(match.group(0), None)
    return tuple(seen)


def scope_connectivity(module: Module, table: ModuleTable) -> tuple[NetEdge, ...]:
    """Compute net-level dataflow among ``module``'s direct children.

    Endpoints are ``module``'s own ports and its direct child
    instances — nothing deeper. Call it once per scope to build a
    nested block diagram.

    Resolution rules:

    * Pin directions come from the child's own
      :class:`~rtl_buddy_view.extractor.Module` in ``table``. A child
      the table doesn't know (a blackbox) has unknown pin directions;
      those pins count as sinks when the group has at least one known
      driver, and are dropped otherwise — better an under-drawn edge
      than a fabricated direction.
    * Positional connections are skipped: without the child's port
      order being authoritative (parameters, ANSI vs non-ANSI headers)
      guessing the port would invent connectivity.
    * Implicit ``.name`` shorthand binds to the same-named net, which
      is exactly what the elaborator does.
    * An interface port has no direction, so it joins its group as a
      driver when the group has known sinks and as a sink when it has
      known drivers — both when it has both, since an interface bundle
      genuinely carries traffic in both directions. With neither it
      defaults to a driver (an unconsumed bundle still reads as an
      input to the block).
    * A driver only reaches a sink whose root is reachable from the
      driver's root along the assign flow graph (reflexively, so a
      shared net needs no assign at all). A pin driving the *left*
      side of an assign is therefore not a driver of anything the RHS
      feeds — that direction would be a second driver on the net.
    * Groups whose every net name looks like a clock or a reset are
      dropped wholesale. "Group" here is a weakly connected component
      of the flow graph, so the filter sees exactly the net set an
      undirected alias model would have built.

    Returned edges are bundled per endpoint pair and sorted by
    ``(src, dst)``.
    """
    # Pins first so their roots exist as flow-graph nodes before the
    # assign pass wires them; isolated roots still need a component of
    # their own for the direct same-net case.
    pins = _collect_pins(module, table)
    flow = _FlowGraph()
    for pin in pins:
        for root in pin.roots:
            flow.add(root)
    for port in module.ports:
        flow.add(port.name)
    for assign in module.assigns:
        lhs_roots = root_identifiers(assign.lhs_text)
        rhs_roots = root_identifiers(assign.rhs_text)
        for lhs in lhs_roots:
            for rhs in rhs_roots:
                flow.connect(rhs, lhs)

    groups = _build_groups(module, pins, flow)

    # (src, dst) -> net names, accumulated across every group that
    # produced the pair so a bundle carries all of its signals.
    bundled: dict[tuple[Endpoint, Endpoint], set[str]] = {}
    for group in groups.values():
        if _is_clock_or_reset_group(group.names):
            continue
        for src, dst, nets in group.edges(flow):
            bundled.setdefault((src, dst), set()).update(nets)

    return tuple(
        NetEdge(src=src, dst=dst, nets=tuple(sorted(nets)))
        for (src, dst), nets in sorted(bundled.items())
    )


# --- internals ---------------------------------------------------------------


@dataclass(frozen=True)
class _Pin:
    """One resolved child-instance pin: where it lands and which way."""

    endpoint: Endpoint
    direction: _Direction | None
    roots: tuple[str, ...]


class _FlowGraph:
    """Directed value-flow graph over net root names.

    Built once from the scope's continuous assigns, then queried; the
    two derived views (components, reachability) are memoised and
    assume the edge set is frozen by the time they are first asked
    for. Small enough to inline rather than reach for a dependency
    (AGENTS.md § no new top-level deps).
    """

    def __init__(self) -> None:
        self._succ: dict[str, set[str]] = {}
        self._pred: dict[str, set[str]] = {}
        self._components: dict[str, str] | None = None
        self._reachable: dict[str, frozenset[str]] = {}

    def add(self, name: str) -> None:
        """Register ``name`` as a node, even if nothing flows through it."""
        self._succ.setdefault(name, set())
        self._pred.setdefault(name, set())

    def connect(self, src: str, dst: str) -> None:
        """Record that a value flows from ``src`` into ``dst``."""
        self.add(src)
        self.add(dst)
        if src == dst:
            return
        self._succ[src].add(dst)
        self._pred[dst].add(src)

    def component_of(self, name: str) -> str:
        """Return the representative of ``name``'s weakly connected component.

        Weak (direction-ignoring) connectivity is exactly the alias
        grouping an undirected model would produce — which is what the
        clock/reset filter and the interface/blackbox role resolution
        are calibrated against. The representative is the component's
        lexicographic minimum, so grouping never depends on set order.
        """
        if self._components is None:
            self._components = self._compute_components()
        return self._components.get(name, name)

    def reachable_from(self, name: str) -> frozenset[str]:
        """Return ``name`` plus everything downstream of it.

        Reflexive (a net always reaches itself, assign or not) and
        transitive; cycles terminate because the search is per-source
        with its own visited set.
        """
        cached = self._reachable.get(name)
        if cached is not None:
            return cached
        seen = {name}
        stack = [name]
        while stack:
            current = stack.pop()
            for nxt in self._succ.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        result = frozenset(seen)
        self._reachable[name] = result
        return result

    def _compute_components(self) -> dict[str, str]:
        representative: dict[str, str] = {}
        # Sorted starts make the seed of each component its
        # lexicographic minimum: every member is unvisited when the
        # smallest of them comes up.
        for start in sorted(self._succ):
            if start in representative:
                continue
            representative[start] = start
            stack = [start]
            while stack:
                current = stack.pop()
                for neighbour in self._succ[current] | self._pred[current]:
                    if neighbour not in representative:
                        representative[neighbour] = start
                        stack.append(neighbour)
        return representative


class _Group:
    """One weakly connected component: its endpoints and their roles."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.drivers: dict[Endpoint, set[str]] = {}
        self.sinks: dict[Endpoint, set[str]] = {}
        self.unknown: dict[Endpoint, set[str]] = {}
        self.interfaces: dict[Endpoint, set[str]] = {}

    def add(
        self,
        bucket: dict[Endpoint, set[str]],
        endpoint: Endpoint,
        nets: tuple[str, ...],
    ) -> None:
        bucket.setdefault(endpoint, set()).update(nets)
        self.names.update(nets)

    def edges(self, flow: _FlowGraph) -> list[tuple[Endpoint, Endpoint, set[str]]]:
        """Resolve roles into ``(src, dst, nets)`` triples.

        A pair is emitted only when something the driver sources
        reaches something the sink reads in ``flow``. ``nets`` is the
        reached subset of the *sink* side: the net a receiver is
        actually bound to is the one worth labelling the edge with.
        """
        drivers = dict(self.drivers)
        sinks = dict(self.sinks)
        for endpoint, nets in self.interfaces.items():
            # Bidirectional by construction — see the interface-port
            # rule in :func:`scope_connectivity`.
            if self.sinks or not self.drivers:
                drivers.setdefault(endpoint, set()).update(nets)
            if self.drivers:
                sinks.setdefault(endpoint, set()).update(nets)
        if drivers:
            for endpoint, nets in self.unknown.items():
                sinks.setdefault(endpoint, set()).update(nets)
        out: list[tuple[Endpoint, Endpoint, set[str]]] = []
        for src, src_nets in drivers.items():
            downstream: set[str] = set()
            for root in src_nets:
                downstream |= flow.reachable_from(root)
            for dst, dst_nets in sinks.items():
                if src == dst:
                    continue
                reached = dst_nets & downstream
                if reached:
                    out.append((src, dst, reached))
        return out


def _collect_pins(module: Module, table: ModuleTable) -> tuple[_Pin, ...]:
    """Resolve every named child-instance pin into a :class:`_Pin`."""
    pins: list[_Pin] = []
    for inst in module.instances:
        child = table.modules_by_name.get(inst.module_name)
        directions: dict[str, _Direction | None] = (
            {p.name: p.direction for p in child.ports} if child is not None else {}
        )
        endpoint: Endpoint = ("inst", inst.name)
        for conn in inst.port_connections:
            if conn.port_name is None:
                # Positional — the child's port order is not reliable
                # enough to bind a name to. See scope_connectivity.
                continue
            # Implicit ``.name`` shorthand: the elaborator binds it to
            # the same-named net, so that is what we resolve it to.
            expr = conn.net_expr_text.strip() or conn.port_name
            roots = root_identifiers(expr)
            if not roots:
                continue
            pins.append(
                _Pin(
                    endpoint=endpoint,
                    direction=directions.get(conn.port_name)
                    if child is not None
                    else None,
                    roots=roots,
                )
            )
    return tuple(pins)


def _build_groups(
    module: Module, pins: tuple[_Pin, ...], flow: _FlowGraph
) -> dict[str, _Group]:
    """Bucket pins and module ports into their flow components."""
    groups: dict[str, _Group] = {}

    def group_for(root: str) -> _Group:
        return groups.setdefault(flow.component_of(root), _Group())

    for pin in pins:
        for root in pin.roots:
            group = group_for(root)
            if pin.direction == "input":
                bucket = group.sinks
            elif pin.direction in ("output", "inout"):
                bucket = group.drivers
            else:
                bucket = group.unknown
            group.add(bucket, pin.endpoint, (root,))

    for port in module.ports:
        group = group_for(port.name)
        endpoint: Endpoint = ("port", port.name)
        if port.direction == "input":
            bucket = group.drivers
        elif port.direction in ("output", "inout"):
            bucket = group.sinks
        else:
            bucket = group.interfaces
        group.add(bucket, endpoint, (port.name,))

    return groups


def _is_clock_or_reset_group(names: set[str]) -> bool:
    """True when every net name in the component looks like a clock/reset.

    A group with even one data-looking name is kept: an
    ``assign en = go & ~clk_locked;`` style alias should not take the
    whole group down with it.
    """
    if not names:
        return True
    return all(
        _CLOCK_NAME_RE.search(name)
        or _RESET_NAME_RE.search(name)
        or _BLOCK_EXTRA_CLOCK_RE.search(name)
        or _BLOCK_EXTRA_RESET_RE.search(name)
        for name in names
    )
