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
  only continuous assignments do — both the ``assign`` statement and
  the ``wire w = expr;`` declaration form.
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

#: The leading ``[msb:lsb]`` of a packed range, integer bounds only.
#: A parameterised bound (``[PTR_W-1:0]``) deliberately fails to
#: match — see :func:`port_width`.
_PACKED_RANGE_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")

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

    ``src_pins`` / ``dst_pins`` name the **formal ports** the bundle
    attaches to at each end — ``("wr_en", "wr_data")`` rather than the
    nets those pins happen to be bound to. For an ``("inst", …)``
    endpoint they are the child module's declared port names; for a
    ``("port", name)`` endpoint the scope's own port is the pin, so
    the tuple is ``(name,)``. Both are deduped and sorted. A pin only
    appears when it participates in *this* bundle: a driver pin whose
    net actually reaches the sink, a sink pin actually reached.

    ``bits`` is the bundle's total width — the sum over ``nets`` of
    each net's width, resolved in the ranked order documented on
    :func:`_net_widths` (driving pin → the scope's own net
    declaration → the scope's own port declaration), always parsed by
    :func:`port_width`. ``None`` when any net in the bundle has an
    unknown width, because a partial sum would read as a real number
    to a consumer drawing bus slashes.

    All three are additive with defaults: the block-diagram renderer
    predates them and its output is unchanged.
    """

    src: Endpoint
    dst: Endpoint
    nets: tuple[str, ...]
    src_pins: tuple[str, ...] = ()
    dst_pins: tuple[str, ...] = ()
    bits: int | None = None


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


def port_width(type_text: str | None) -> int | None:
    """Declared bit width of a port from its verbatim type slice.

    ``logic [7:0]`` → 8, ``logic`` → 1, ``logic [PTR_W-1:0]`` → None,
    ``None`` → None::

        port_width("logic [18:0]")    == 19
        port_width("wire")            == 1
        port_width("logic [W-1:0]")   is None

    No expression evaluation and no type resolution — a parameterised
    bound is reported as *unknown* rather than guessed, because a
    wrong bus width on a schematic is worse than an unlabelled wire.
    Only the leading packed range is read: for
    ``logic [3:0][7:0]`` (a packed array) the first dimension is
    returned, which is the number of elements, not the bit count —
    a documented under-report rather than a silent wrong total.

    Callers holding a :class:`~rtl_buddy_view.extractor.Port` should
    additionally treat ``port_kind != "wire"`` as unknown: an
    interface bundle has no width, and this function would read its
    range-less type slice as a scalar.
    """
    if type_text is None:
        return None
    if "[" in type_text:
        match = _PACKED_RANGE_RE.search(type_text)
        if match is None:
            return None
        msb, lsb = int(match.group(1)), int(match.group(2))
        return abs(msb - lsb) + 1
    if not type_text.strip():
        return None
    return 1


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
    ``(src, dst)``. Each carries the formal pin names it attaches to
    (``src_pins`` / ``dst_pins``) and, when every net's width is
    knowable, the bundle's total ``bits`` — both captured here, where
    the binding site is still in hand, rather than re-derived by a
    renderer intersecting net names after the fact. Widths come from
    the ranked sources :func:`_net_widths` documents, so a net that
    only an ``assign`` drives is still measured, from its own
    declaration.
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
    widths = _net_widths(module, pins)

    # (src, dst) -> (net names, src formals, dst formals), accumulated
    # across every group that produced the pair so a bundle carries
    # all of its signals and all of the pins they land on.
    bundled: dict[tuple[Endpoint, Endpoint], tuple[set[str], set[str], set[str]]] = {}
    for group in groups.values():
        if _is_clock_or_reset_group(group.names):
            continue
        for src, dst, nets, src_pins, dst_pins in group.edges(flow):
            slot = bundled.setdefault((src, dst), (set(), set(), set()))
            slot[0].update(nets)
            slot[1].update(src_pins)
            slot[2].update(dst_pins)

    return tuple(
        NetEdge(
            src=src,
            dst=dst,
            nets=tuple(sorted(nets)),
            src_pins=tuple(sorted(src_pins)),
            dst_pins=tuple(sorted(dst_pins)),
            bits=_bundle_bits(nets, widths),
        )
        for (src, dst), (nets, src_pins, dst_pins) in sorted(bundled.items())
    )


# --- internals ---------------------------------------------------------------


@dataclass(frozen=True)
class _Pin:
    """One resolved child-instance pin: where it lands and which way.

    ``formal`` is the child's own port name (the ``.q`` of ``.q(w)``)
    — the schematic-facing identity that survives into
    :attr:`NetEdge.src_pins`. ``type_text`` is that port's declared
    type slice, kept for :func:`port_width`.
    """

    endpoint: Endpoint
    direction: _Direction | None
    roots: tuple[str, ...]
    formal: str
    type_text: str | None = None


#: ``endpoint -> net root -> formal pin names``. One bucket per role
#: (driver / sink / unknown / interface) inside a :class:`_Group`.
#: Nested by root because an edge is resolved per *reached root*, and
#: only the pins on those roots belong on it.
_Bucket = dict[Endpoint, dict[str, set[str]]]


def _merge_bucket(target: _Bucket, extra: _Bucket) -> None:
    """Fold ``extra`` into ``target`` in place (union at every level)."""
    for endpoint, roots in extra.items():
        slot = target.setdefault(endpoint, {})
        for root, formals in roots.items():
            slot.setdefault(root, set()).update(formals)


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
        self.drivers: _Bucket = {}
        self.sinks: _Bucket = {}
        self.unknown: _Bucket = {}
        self.interfaces: _Bucket = {}

    def add(
        self,
        bucket: _Bucket,
        endpoint: Endpoint,
        root: str,
        formal: str,
    ) -> None:
        bucket.setdefault(endpoint, {}).setdefault(root, set()).add(formal)
        self.names.add(root)

    def edges(
        self, flow: _FlowGraph
    ) -> list[tuple[Endpoint, Endpoint, set[str], set[str], set[str]]]:
        """Resolve roles into ``(src, dst, nets, src_pins, dst_pins)``.

        A pair is emitted only when something the driver sources
        reaches something the sink reads in ``flow``. ``nets`` is the
        reached subset of the *sink* side: the net a receiver is
        actually bound to is the one worth labelling the edge with.

        The pin sets are filtered by the same reachability, so a
        driver pin on a net that feeds nothing on this sink stays off
        the edge even when the same endpoint pair is connected
        elsewhere in the group.
        """
        drivers: _Bucket = {}
        sinks: _Bucket = {}
        _merge_bucket(drivers, self.drivers)
        _merge_bucket(sinks, self.sinks)
        for endpoint, roots in self.interfaces.items():
            # Bidirectional by construction — see the interface-port
            # rule in :func:`scope_connectivity`.
            if self.sinks or not self.drivers:
                _merge_bucket(drivers, {endpoint: roots})
            if self.drivers:
                _merge_bucket(sinks, {endpoint: roots})
        if drivers:
            _merge_bucket(sinks, self.unknown)
        out: list[tuple[Endpoint, Endpoint, set[str], set[str], set[str]]] = []
        for src, src_roots in drivers.items():
            downstream_of = {root: flow.reachable_from(root) for root in src_roots}
            downstream: set[str] = set()
            for reach in downstream_of.values():
                downstream |= reach
            for dst, dst_roots in sinks.items():
                if src == dst:
                    continue
                reached = set(dst_roots) & downstream
                if not reached:
                    continue
                src_pins = {
                    formal
                    for root, formals in src_roots.items()
                    if downstream_of[root] & reached
                    for formal in formals
                }
                dst_pins = {formal for root in reached for formal in dst_roots[root]}
                out.append((src, dst, reached, src_pins, dst_pins))
        return out


def _collect_pins(module: Module, table: ModuleTable) -> tuple[_Pin, ...]:
    """Resolve every named child-instance pin into a :class:`_Pin`."""
    pins: list[_Pin] = []
    for inst in module.instances:
        child = table.modules_by_name.get(inst.module_name)
        directions: dict[str, _Direction | None] = (
            {p.name: p.direction for p in child.ports} if child is not None else {}
        )
        types: dict[str, str | None] = (
            {p.name: p.type_text for p in child.ports if p.port_kind == "wire"}
            if child is not None
            else {}
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
                    formal=conn.port_name,
                    type_text=types.get(conn.port_name),
                )
            )
    return tuple(pins)


def _net_widths(module: Module, pins: tuple[_Pin, ...]) -> dict[str, int]:
    """Width of each net root, resolved through three ranked sources.

    In precedence order, first tier that yields an unambiguous number
    wins:

    1. **The driving pin.** A net is as wide as whatever drives it
       declares itself to be, so the lookup runs from the driving
       side: a child ``output`` / ``inout`` pin, or — for a net
       arriving from outside this scope — the enclosing module's own
       ``input`` port.
    2. **The scope's own net declaration.** ``assign payload = {…};``
       has no driving pin at all, but ``logic [18:0] payload;`` sits
       right there in the module body. This is what makes an
       assign-driven bundle drawable.
    3. **The scope's own port declaration**, in any direction. Tier 1
       already covers ``input``; this adds the case of a net that
       *is* an ``output`` / ``inout`` port of the scope and is driven
       by an assign rather than by a pin.

    A tier abstains (falls through to the next) when it saw the name
    but could not pin one number to it:

    * a source whose declared type has a parameterised bound, an
      interface kind, or no type at all;
    * a driver bound to a multi-root expression (``.q({a, b})``),
      where the port's width belongs to no single net;
    * two sources in the same tier disagreeing — two drivers on one
      net, or one name declared at two widths in two generate scopes.
      Under a structural model that is more likely a slice binding or
      a shadow than a real conflict, and either way not a number
      worth printing on a wire.

    A tier never overrides a lower-numbered one, so this is purely
    additive: a bundle that had a width keeps exactly the width it
    had.

    A slice binding (``.q(w[3:0])``) reports the *port's* width as the
    net's, which over-states a wide net driven piecewise. That is the
    same trade the rest of this module makes: bit ranges are ignored.
    """
    resolved: dict[str, int] = {}
    for tier in (
        _driver_widths(module, pins),
        _decl_widths(module),
        _port_widths(module),
    ):
        for root, width in tier.items():
            resolved.setdefault(root, width)
    return resolved


def _resolve(candidates: dict[str, set[int | None]]) -> dict[str, int]:
    """Keep only the names one tier pinned to exactly one known width."""
    return {
        root: next(iter(widths))  # type: ignore[misc]
        for root, widths in candidates.items()
        if len(widths) == 1 and None not in widths
    }


def _driver_widths(module: Module, pins: tuple[_Pin, ...]) -> dict[str, int]:
    """Tier 1 — widths declared by whatever drives the net."""
    candidates: dict[str, set[int | None]] = {}
    for pin in pins:
        if pin.direction not in ("output", "inout"):
            continue
        if len(pin.roots) != 1:
            for root in pin.roots:
                candidates.setdefault(root, set()).add(None)
            continue
        candidates.setdefault(pin.roots[0], set()).add(port_width(pin.type_text))
    for port in module.ports:
        if port.direction != "input":
            continue
        width = port_width(port.type_text) if port.port_kind == "wire" else None
        candidates.setdefault(port.name, set()).add(width)
    return _resolve(candidates)


def _decl_widths(module: Module) -> dict[str, int]:
    """Tier 2 — widths from the scope's own net/variable declarations."""
    candidates: dict[str, set[int | None]] = {}
    for decl in module.net_decls:
        candidates.setdefault(decl.name, set()).add(port_width(decl.type_text))
    return _resolve(candidates)


def _port_widths(module: Module) -> dict[str, int]:
    """Tier 3 — widths from the scope's own port declarations."""
    candidates: dict[str, set[int | None]] = {}
    for port in module.ports:
        width = port_width(port.type_text) if port.port_kind == "wire" else None
        candidates.setdefault(port.name, set()).add(width)
    return _resolve(candidates)


def _bundle_bits(nets: set[str], widths: dict[str, int]) -> int | None:
    """Total width of a bundle, or ``None`` when any net is unknown."""
    total = 0
    for net in nets:
        width = widths.get(net)
        if width is None:
            return None
        total += width
    return total


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
            group.add(bucket, pin.endpoint, root, pin.formal)

    for port in module.ports:
        group = group_for(port.name)
        endpoint: Endpoint = ("port", port.name)
        if port.direction == "input":
            bucket = group.drivers
        elif port.direction in ("output", "inout"):
            bucket = group.sinks
        else:
            bucket = group.interfaces
        # The scope's own port *is* the pin at that endpoint, so the
        # formal name is the port name.
        group.add(bucket, endpoint, port.name, port.name)

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
