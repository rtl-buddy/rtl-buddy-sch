"""Scope-local net connectivity — sibling-to-sibling dataflow.

The hierarchy graph (:mod:`rtl_buddy_view.graph`) answers *what
instantiates what*. A block diagram needs the orthogonal question:
*within one module, which child talks to which other child, and over
what net*. That is what this module computes.

Everything here is a pure function over the in-memory
:class:`~rtl_buddy_view.extractor.ModuleTable` — no I/O, no toolchain
dependency, deterministic output (see AGENTS.md § Development rules).

The algorithm is deliberately structural rather than semantic:

1. Every child-instance pin is mapped to the **net keys** of the
   expression it is bound to. For an ordinary net that is its root
   identifier — ``hwif_out.ctrl.SRC.value`` and ``hwif_out.op.OP.value``
   (a struct) both reduce to ``hwif_out``, and ``cmd_data[18:16]``
   reduces to ``cmd_data``. A member of an **interface instance** is a
   net of its own, so it keeps its member path instead: ``cfg.a_en``,
   ``q[1].c_sta`` (see :func:`net_keys`). Collapsing those to ``cfg`` /
   ``q`` would put every block touching any member — or any element
   of an interface array — on one net (rtl-buddy-sch#193). An element
   of a plain vector selected by a constant index is likewise keyed
   ``dv[SR_A]`` (rtl-buddy-sch#208), and identifiers inside an index
   are never nets. A whole reference (``cfg``, ``dv``) also stands on
   every member or element key the scope selects, so it meets them
   all while two elements stay apart.
2. Continuous and procedural assignments build a **directed
   value-flow graph** over those roots: ``assign lhs = rhs;`` adds one
   edge ``rhs_root -> lhs_root`` per root pair, because value flows out
   of the RHS and into the LHS. Chains compose, so ``assign c = b;
   assign b = a;`` makes ``c`` reachable from ``a`` but not the other
   way round. An assignment in an ``always*`` block (``q <= d;``,
   ``c = a;``) adds the same edges (rtl-buddy-sch#210), so a root input
   the scope registers before handing on still reaches the instance it
   feeds. Only the assigned value counts — the ``if`` / ``case``
   conditions that steer it do not, or the reset in ``if (!rst_n)``
   would join every register's group and defeat the clock/reset
   filter.
3. Pins with a known ``output`` / ``inout`` direction are drivers and
   ``input`` pins are sinks; the enclosing module's own ports join in
   from the outside (``input`` port = driver, ``output`` / ``inout``
   port = sink). A driver **sources** its roots; a sink **reads**
   its roots. A pin on an interface port takes its modport's folded
   direction (:func:`modport_direction`, rtl-buddy-sch#194).
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
* A member or element is keyed one level deep, by its spelling:
  ``dv[0]`` never meets ``dv[ZERO]``, and an interface member under a
  non-constant index (``q[i].v``) only meets the same text, never
  ``q[0].v``. Constant-ness is judged without elaborating: an index is
  constant unless it is a range or names a net, variable, port or
  interface of the scope, so a **genvar** index counts as constant and
  meets only the same spelling.
* A vector select by a range (``bus[7:0]``) or a variable index
  (``mem[addr]``) falls back to the whole vector, so it meets every
  element key — two disjoint slices of one vector still alias. The
  index itself (``addr``) is never a read.
* Flow comes from assignments only: the ``assign`` statement, the
  ``wire w = expr;`` declaration form, and assignments in ``always*``
  blocks. ``if`` / ``case`` / loop conditions contribute none (a data
  enable read only as a condition gets no edge), while a reset written
  *into* the value (``q <= rst_n ? d : '0;``) is flow like any other
  operand. Assignments in functions, tasks and ``initial`` blocks are
  not read, and a procedural assignment is flow whatever its timing:
  a register and a wire look the same.
* A whole-interface binding's single direction is a heuristic fold
  of its modport (handshake names out, majority by signal count); a
  tie leaves it unresolved rather than guessed.
* Clock/reset filtering is name-shaped, applied to the **weakly**
  connected components of the flow graph (see
  :func:`_is_clock_or_reset_group`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
import dataclasses
from dataclasses import dataclass
from typing import Literal, TypeVar

from rtl_buddy_view.extractor import (
    Interface,
    Modport,
    Module,
    ModuleTable,
    ParameterOverride,
    Port,
)
from rtl_buddy_view.hints import NetHints, PinDirection
from rtl_buddy_view.width_expr import (
    IDENT_RE,
    sum_width_exprs,
    width_expr_of,
    width_of,
)

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

#: One underscore-delimited token of a signal name that marks it as a
#: **return path** — the handshake half of an interface that flows
#: against its data (``ready``/``rdy`` in any prefixed or suffixed
#: spelling: ``tready``, ``awready``, ``hreadyout``, ``rdy_n``; and
#: the bare ``ack`` / ``gnt`` / ``grant`` / ``credit`` / ``stall`` /
#: ``wait`` family). Consulted only by :func:`modport_direction`,
#: which votes on a modport's data signals and leaves these out.
_HANDSHAKE_TOKEN_RE = re.compile(
    r"(?i)^(?:.*(?:ready|rdy)(?:out|in|o|i|n)?"
    r"|ack|gnt|grant|credits?|stall|wait|waitrequest)$"
)

#: Modport names that declare a passive observer outright. The
#: structural test in :func:`is_observer_modport` catches the rest.
_OBSERVER_MODPORT_NAMES = frozenset(
    {"mon", "monitor", "obs", "observer", "passive", "snoop", "sniff"}
)

#: The leading ``[msb:lsb]`` of a packed range, integer bounds only.
#: A parameterised bound (``[PTR_W-1:0]``) deliberately fails to
#: match — see :func:`port_width`.
_PACKED_RANGE_RE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]")

#: An identifier token that is *not* a field select (not preceded by
#: ``.``), not the base part of a sized literal (not preceded by
#: ``'``), and not a system task/function name (not preceded by ``$``).
#: The lookbehind also excludes identifier characters so the tail of a
#: longer identifier never matches on its own.
#:
#: Defined in :mod:`rtl_buddy_view.width_expr`, which asks the same
#: lexical question of a width bound ("which names here could a
#: parameter stand in for") that this module asks of a net expression
#: ("which names here are roots"). One pattern, one place.
_ROOT_IDENT_RE = IDENT_RE

#: ``.member`` after a select chain, whitespace allowed around the dot.
#: Matched by :func:`_scan_tail` once the leading ``[index]`` groups
#: are consumed.
_TAIL_MEMBER_RE = re.compile(r"\s*\.\s*([A-Za-z_][A-Za-z0-9_$]*)")

#: The leading identifier of a net key — its root.
_KEY_ROOT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")

_WS_RE = re.compile(r"\s+")

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
    :func:`_net_width_info` (driving pin → the scope's own net
    declaration → the scope's own port declaration), always parsed by
    :func:`port_width`. ``None`` when any net in the bundle has an
    unknown width, because a partial sum would read as a real number
    to a consumer drawing bus slashes. **Numeric only**: a bundle
    whose width is knowable in symbols but not in integers leaves this
    ``None``.

    ``bits_expr`` is the **independent** algebraic answer — the
    bundle's width written in the names the declarations use
    (``"PTR_W"``, ``"WIDTH+1"``), read off the ranges as written with
    no parameter substitution. It is *not* mutually exclusive with
    ``bits``: a bundle of one ``[WIDTH-1:0]`` net bound
    ``#(.WIDTH(19))`` is both ``bits=19`` and ``bits_expr="WIDTH"``,
    and the consumer decides which to show (the canvas prefers the
    expression — the name says which knob moves the bus, the number is
    one instantiation's detail). ``None`` when no net contributes a
    symbol, when a net is unknown both ways, or when the expression
    would not be clean — see
    :func:`rtl_buddy_view.width_expr.sum_width_exprs` for the
    abstention rules.

    All of them are additive with defaults: the block-diagram renderer
    predates them and its output is unchanged.

    ``emphasis`` is the author's statement about how much this edge
    matters — ``"main"`` for the primary datapath, ``"side"`` for CSR
    and status wiring — read off ``rbsch`` net hints (epic #159 phase
    2). Resolved here rather than in a renderer because the nets are
    still in hand: an edge is emphasized when any of its nets is, and
    ``main`` beats ``side`` within one bundle (the author called part
    of it the money path; thinning the whole edge would hide that).
    ``None`` — the default, and the only value when no hints are
    supplied — leaves every consumer unchanged.

    ``bundle`` (phase 3) is the author's name for this wire group,
    set when **every** net on the edge carries the same ``bundle=``
    hint — all-or-nothing, because naming an edge that also carries
    unrelated nets would claim more than the author said. A named
    return path folds into the data direction (see
    ``_fold_bundle_returns``), so a valid/ready pair reads as one
    thick edge; ``bits`` keeps describing the kept direction's nets
    only — the slash on a bus is its payload width, not payload plus
    handshake.
    """

    src: Endpoint
    dst: Endpoint
    nets: tuple[str, ...]
    src_pins: tuple[str, ...] = ()
    dst_pins: tuple[str, ...] = ()
    bits: int | None = None
    bits_expr: str | None = None
    emphasis: Literal["main", "side"] | None = None
    bundle: str | None = None


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
    ``mem[addr]`` yields both ``mem`` and ``addr``: this is the purely
    lexical scan. The connectivity keys (:func:`net_keys`) leave index
    identifiers out (rtl-buddy-sch#208).

    The result is deduped, in first-appearance order, so callers get
    deterministic output without needing to sort.
    """
    seen: dict[str, None] = {}
    for match in _ROOT_IDENT_RE.finditer(expr_text):
        seen.setdefault(match.group(0), None)
    return tuple(seen)


def resolve_interface_port(port: Port, table: ModuleTable) -> Port:
    """``port``, reclassified as an interface port when it is one.

    Verible cannot tell a **modport-less interface port** (``bus_if
    gen``) from a net port of a user-defined type: both are a type
    name followed by an identifier, so the frontend reports a ``wire``
    port with no direction and ``type_text == "bus_if"``. The filelist
    settles it — when that type text is exactly the name of an
    interface the frontend parsed, the port is an interface port with
    no modport, which is how this returns it. Anything else (a
    declared direction, a type with dimensions or a package scope, a
    type that names no parsed interface) comes back unchanged.
    """
    if port.port_kind != "wire" or port.direction is not None:
        return port
    type_name = (port.type_text or "").strip()
    if type_name not in table.interfaces_by_name:
        return port
    return dataclasses.replace(
        port, port_kind="interface", interface_type=type_name, modport=None
    )


def _resolved_ports(module: Module, table: ModuleTable) -> Module:
    """``module`` with :func:`resolve_interface_port` applied to its ports."""
    ports = tuple(resolve_interface_port(p, table) for p in module.ports)
    if all(a is b for a, b in zip(ports, module.ports)):
        return module
    return dataclasses.replace(module, ports=ports)


def interface_roots(module: Module, table: ModuleTable) -> frozenset[str]:
    """Names in ``module``'s scope that denote an interface, not a net.

    Two declarations make a name an interface rather than a net or a
    struct-typed variable:

    * a body instance whose module is an **interface** the frontend
      parsed (``cfg_if cfg ();`` with ``cfg_if`` in
      :attr:`~rtl_buddy_view.extractor.ModuleTable.interfaces_by_name`)
      — interface arrays (``cfg_if q [2] ();``) included;
    * a port of the scope itself with ``port_kind == "interface"``
      (``bus_if.snk in_bus``).

    A struct-typed ``logic`` is a :class:`~rtl_buddy_view.extractor.NetDecl`
    or a wire port, never either of these, so ``s.field`` keeps
    reducing to ``s``. An instance of an interface the filelist does
    not define is not recognised (there is nothing to tell it from a
    blackbox module) and keeps today's root-name behaviour.
    """
    names = {
        inst.name
        for inst in module.instances
        if inst.module_name in table.interfaces_by_name
    }
    names.update(p.name for p in module.ports if p.port_kind == "interface")
    # A generate scope is a namespace the same way (rtl-buddy-sch#209):
    # an unrolled ``g[0].u_s`` binds the iteration's own ``g[0].w``,
    # which must key per iteration rather than collapse onto ``g``.
    names.update(_key_root(inst.name) for inst in module.instances if "." in inst.name)
    return frozenset(names)


def net_keys(
    expr_text: str,
    interfaces: frozenset[str] | set[str] = frozenset(),
    variables: frozenset[str] | set[str] = frozenset(),
) -> tuple[str, ...]:
    """The nets an expression slice references, as connectivity keys.

    :func:`root_identifiers`, with two refinements.

    A reference into an **interface** named in ``interfaces`` (see
    :func:`interface_roots`) keeps its member path — the member is the
    net, the interface is only a namespace (rtl-buddy-sch#193)::

        net_keys("cfg.a_en", {"cfg"})            ->  ("cfg.a_en",)
        net_keys("q[1].c_sta", {"q"})            ->  ("q[1].c_sta",)
        net_keys("q[i].c_sta", {"q"})            ->  ("q[i].c_sta",)
        net_keys("cfg.data[3:0]", {"cfg"})       ->  ("cfg.data",)
        net_keys("cfg", {"cfg"})                 ->  ("cfg",)    # whole

    An **element select of a plain vector** with a constant index is
    keyed on the element, the same way (rtl-buddy-sch#208)::

        net_keys("dv[SR_A]")                     ->  ("dv[SR_A]",)
        net_keys("dv[0].f")                      ->  ("dv[0]",)
        net_keys("bus[7:0]")                     ->  ("bus",)    # a range
        net_keys("mem[addr]", variables={"addr"})  ->  ("mem",)
        net_keys("s.field")                      ->  ("s",)      # a struct

    An index is **constant** unless it is a range (``[7:0]``,
    ``[i +: 8]``) or names one of ``variables`` — the scope's own nets,
    variables, ports and interfaces (:func:`_scope_variables`). Enum
    members, parameters, localparams and literals are not declared as
    nets, which is what makes them constant here; a genvar is not
    either, so a genvar index is keyed verbatim and only meets the same
    spelling. A non-constant select falls back to the root name.

    Identifiers **inside** ``[...]`` never become keys, constant or
    not: they are reads of the index, not dataflow between instances.
    Selects past the first index or member (``cfg.data[3:0]``,
    ``dv[0][3]``, ``cfg.s.f``) are dropped the way a struct field is.
    Whitespace is removed from a key.
    """
    return _scan_refs(expr_text, interfaces, None, variables)[0]


def _interface_modports(
    module: Module, table: ModuleTable
) -> dict[str, frozenset[str]]:
    """Interface name in ``module``'s scope -> its type's modport names.

    What lets ``.m(u_if.sub)`` — a binding of the ``sub`` modport of
    ``u_if`` — read as a whole-interface binding rather than as a
    member net called ``sub``.
    """
    types = {
        inst.name: inst.module_name
        for inst in module.instances
        if inst.module_name in table.interfaces_by_name
    }
    types.update(
        (p.name, p.interface_type or "")
        for p in module.ports
        if p.port_kind == "interface"
    )
    out: dict[str, frozenset[str]] = {}
    for name, type_name in types.items():
        iface = table.interfaces_by_name.get(type_name)
        if iface is not None and iface.modports:
            out[name] = frozenset(mp.name for mp in iface.modports)
    return out


def _scan_refs(
    expr_text: str,
    interfaces: frozenset[str] | set[str],
    modports: Mapping[str, frozenset[str]] | None = None,
    variables: frozenset[str] | set[str] = frozenset(),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(keys, whole)`` for one expression slice.

    ``keys`` is :func:`net_keys`. ``whole`` lists the references that
    stand for a whole bundle — an interface as its select prefix
    (``cfg``, ``q[1]``), a vector referenced by its bare name or by a
    select that fell back to it (``bus``, ``bus[7:0]``, ``mem[addr]``)
    — so the caller can attach such a binding to the member or element
    nets it spans (see :func:`_expand_whole`). ``modports`` names each
    interface's modports (:func:`_interface_modports`): a select of
    one (``u_if.sub``) is a whole-interface reference too.
    """
    keys: dict[str, None] = {}
    whole: dict[str, None] = {}
    # Everything up to here belongs to a select chain already folded
    # into a key: its index identifiers and member names (including a
    # member spelled ``cfg . a_en``, which the identifier regex does
    # not skip on its own) are not nets.
    consumed = 0
    scoped = _generate_scoped(interfaces)
    for match in _ROOT_IDENT_RE.finditer(expr_text):
        if match.start() < consumed:
            continue
        name = match.group(0)
        end = match.end()
        if scoped and name in interfaces:
            name, end = _scoped_name(expr_text, match.start(), name, end, scoped)
        tail = _scan_tail(expr_text, end)
        consumed = tail.end
        indices = "".join(f"[{_WS_RE.sub('', i)}]" for i in tail.indices)
        if name not in interfaces:
            if tail.indices and _constant_index(tail.indices[0], variables):
                keys.setdefault(f"{name}[{_WS_RE.sub('', tail.indices[0])}]", None)
            else:
                keys.setdefault(name, None)
                whole.setdefault(name, None)
            continue
        member = tail.member
        if member is not None and modports and member in modports.get(name, ()):
            member = None
        if member is None:
            keys.setdefault(name, None)
            whole.setdefault(name + indices, None)
            continue
        keys.setdefault(f"{name}{indices}.{member}", None)
    return tuple(keys), tuple(whole)


def _generate_scoped(interfaces: frozenset[str] | set[str]) -> tuple[str, ...]:
    """The generate-scoped interface names among ``interfaces``, longest first.

    An interface instance declared inside a generate block is unrolled
    to a qualified name (``g[0].sub``, rtl-buddy-sch#218) — the only
    dotted entries :func:`interface_roots` produces.
    """
    return tuple(sorted((n for n in interfaces if "." in n), key=len, reverse=True))


def _scoped_name(
    text: str, start: int, name: str, end: int, scoped: tuple[str, ...]
) -> tuple[str, int]:
    """``(name, end)``, widened to a generate-scoped interface at ``start``.

    The unroller spells a reference to a generate-local interface as
    its qualified name verbatim (``g[0].sub[1].v``), so the root is the
    whole ``g[0].sub`` — keyed like a module-scope ``sub`` would be
    (``g[0].sub[1].v``, a whole ``g[0].sub[1]``) — rather than the
    scope label ``g`` with ``sub`` as its member.
    """
    for qualified in scoped:
        stop = start + len(qualified)
        if text.startswith(qualified, start) and not (
            stop < len(text) and (text[stop].isalnum() or text[stop] in "_$")
        ):
            return qualified, stop
    return name, end


@dataclass(frozen=True)
class _Tail:
    """The select chain after a root identifier (:func:`_scan_tail`).

    ``indices`` are the bracket contents before the first member,
    verbatim; ``member`` is that member's name, when there is one;
    ``end`` is the offset just past the whole chain, later selects
    included.
    """

    indices: tuple[str, ...]
    member: str | None
    end: int


def _scan_tail(text: str, pos: int) -> _Tail:
    """Read the ``[...]`` / ``.member`` select chain starting at ``pos``.

    Brackets nest (``q[a[0]].v``). An unbalanced ``[`` ends the chain
    where it starts.
    """
    indices: list[str] = []
    end = _scan_brackets(text, pos, indices)
    match = _TAIL_MEMBER_RE.match(text, end)
    if match is None:
        return _Tail(tuple(indices), None, end)
    member = match.group(1)
    end = match.end()
    while True:
        end = _scan_brackets(text, end, None)
        more = _TAIL_MEMBER_RE.match(text, end)
        if more is None:
            break
        end = more.end()
    return _Tail(tuple(indices), member, end)


def _scan_brackets(text: str, pos: int, out: list[str] | None) -> int:
    """Consume consecutive balanced ``[...]`` groups from ``pos``.

    Appends each group's contents to ``out`` (when given) and returns
    the offset past the last one — ``pos`` itself when none starts
    there.
    """
    end = pos
    while True:
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text) or text[start] != "[":
            return end
        depth = 0
        close = -1
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close < 0:
            return end
        if out is not None:
            out.append(text[start + 1 : close])
        end = close + 1


def _constant_index(index: str, variables: frozenset[str] | set[str]) -> bool:
    """True for an element index that names one fixed element.

    A range (``7:0``, ``i +: 8``; a package scope ``pkg::C`` is not
    one) or an index reading any of the scope's ``variables`` selects
    no fixed element. An empty index is not constant either.
    """
    if not index.strip() or ":" in index.replace("::", ""):
        return False
    return not any(m.group(0) in variables for m in _ROOT_IDENT_RE.finditer(index))


def _scope_variables(module: Module, interfaces: frozenset[str]) -> frozenset[str]:
    """Names in ``module``'s scope whose value can change at run time.

    Its declared nets and variables, its ports, its interfaces and
    anything an assignment writes (an implicit net, a block-local
    variable, a ``for`` loop variable) — the names that make an index
    non-constant (:func:`_constant_index`).
    """
    names = {decl.name for decl in module.net_decls}
    names.update(port.name for port in module.ports)
    names.update(interfaces)
    for assign in (*module.assigns, *module.procedural_assigns):
        names.update(
            _key_root(key) for key in _scan_refs(assign.lhs_text, frozenset())[0]
        )
    return frozenset(names)


def logic_nets(
    module: Module, net_hints: Mapping[str, NetHints] | None = None
) -> frozenset[str]:
    """Root names ``module``'s own logic reads or writes, clocks aside.

    Every identifier in a continuous or procedural assignment (either
    side, index expressions included) or in a procedural condition
    (:attr:`~rtl_buddy_view.extractor.Module.procedural_conditions`),
    less the names the block-diagram classifier reads as a clock or a
    reset (:func:`_is_clock_or_reset_group`, hints included).

    The ``--prune-flags`` guard (rtl-buddy-sch#210): a root port named
    here is real dataflow even when no edge lands on its flag — an
    enable read only in an ``if``, or anything else the textual flow
    model misses — so it must not be pruned like an unconnected clock.
    """
    texts = [
        text
        for assign in (*module.assigns, *module.procedural_assigns)
        for text in (assign.lhs_text, assign.rhs_text)
    ]
    texts.extend(module.procedural_conditions)
    names = {name for text in texts for name in root_identifiers(text)}
    return frozenset(
        name for name in names if not _is_clock_or_reset_group({name}, net_hints)
    )


def _key_root(key: str) -> str:
    """The root identifier of a net key (``q[1].c_sta`` -> ``q``)."""
    match = _KEY_ROOT_RE.match(key)
    return match.group(0) if match is not None else key


def _is_member_key(key: str) -> bool:
    """True for an interface member path rather than a plain net name."""
    return "." in key


def _is_select_key(key: str) -> bool:
    """True for a member path or a vector element (``dv[SR_A]``)."""
    return "." in key or "[" in key


def _is_element_key(key: str) -> bool:
    """True for a plain vector element key (``dv[SR_A]``), not a member."""
    return "[" in key and "." not in key


def _is_handshake_name(name: str) -> bool:
    """True when any token of ``name`` reads as a return-path signal."""
    return any(_HANDSHAKE_TOKEN_RE.match(tok) for tok in name.split("_") if tok)


def _is_clock_or_reset_name(name: str) -> bool:
    """The block-diagram clock/reset name test, on one bare name."""
    return bool(
        _CLOCK_NAME_RE.search(name)
        or _RESET_NAME_RE.search(name)
        or _BLOCK_EXTRA_CLOCK_RE.search(name)
        or _BLOCK_EXTRA_RESET_RE.search(name)
    )


def modport_direction(modport: Modport) -> Literal["input", "output"] | None:
    """Which way a modport's traffic flows, as one port direction.

    A whole-interface binding (``.o(shared)``) is one wire on a block
    diagram, so it needs one direction. The modport says it per
    signal; this folds those into one answer
    (rtl-buddy-sch#194):

    1. Clock- and reset-named signals are dropped — an ``input clk``
       sits in every modport of a synchronous interface and says
       nothing about which way the data goes.
    2. **Return-path signals are dropped** when anything else is
       left: handshake names (``ready``/``rdy`` in any spelling,
       ``ack``, ``gnt``, ``credit``, ``stall``, ``wait``; see
       :data:`_HANDSHAKE_TOKEN_RE`). ``modport src (output v, output
       d, input r)`` is a source — its ``r`` is the reply, the way
       ``bundle=`` folds a valid/ready return path into the data
       direction.
    3. The **majority by signal count** of what remains wins:
       ``"output"`` when outputs outnumber inputs, ``"input"`` the
       other way round. All-output and all-input modports are the
       unanimous case of the same rule; a request/response modport
       (``output addr, wdata, input rdata``) takes its request side.
    4. A **tie**, or a modport with no input or output left to vote
       (all ``inout``, or empty), is ``None`` — unresolved. Callers
       treat that like a modport-less interface port rather than
       guess an arrow.

    ``inout`` signals never vote: they are bidirectional by
    declaration. Width is not weighed (it is often parameterised and
    unresolvable here); the count is the documented, predictable
    rule.
    """
    return _fold_votes(
        [(name, "input") for name in modport.inputs]
        + [(name, "output") for name in modport.outputs]
    )


def _fold_votes(
    votes: Sequence[tuple[str, str]],
) -> Literal["input", "output"] | None:
    """Fold ``(signal name, direction)`` votes into one direction.

    The rule :func:`modport_direction` documents: clock/reset names
    out, handshake names out while anything else is left, then the
    majority by count of ``"input"`` / ``"output"``; anything else
    (``inout``, a tie, no votes) abstains. An empty name is a vote with
    no name to judge (a whole-interface binding) and is always kept.
    """
    voters = [
        (n, d)
        for n, d in votes
        if d in ("input", "output") and not (n and _is_clock_or_reset_name(n))
    ]
    data = [(n, d) for n, d in voters if not (n and _is_handshake_name(n))]
    pool = data or voters
    outputs = sum(1 for _, d in pool if d == "output")
    inputs = len(pool) - outputs
    if outputs > inputs:
        return "output"
    if inputs > outputs:
        return "input"
    return None


def is_observer_modport(iface: Interface, modport: Modport) -> bool:
    """True for a passive monitor modport: it reads, and only reads.

    A modport with no outputs or inouts is a sink — but so is the
    consumer of a valid/data interface with no ready. What makes it a
    **monitor** is that it listens to *both* halves of a handshake:
    it reads a signal that a sink-side modport of the same interface
    drives (``mon`` reads ``r``, which ``snk`` drives). No real
    consumer reads its own reply. A modport *named* like an observer
    (``mon``, ``monitor``, ``passive``, ``snoop``, …) with no outputs
    counts too.

    The block diagram draws a monitor's tap as ``side`` emphasis: the
    dataflow into it is real (and is why the monitor has outputs),
    but it is not the path the reader follows.
    """
    if modport.outputs or modport.inouts or not modport.inputs:
        return False
    if modport.name.lower() in _OBSERVER_MODPORT_NAMES:
        return True
    reads = set(modport.inputs)
    return any(
        other.name != modport.name
        and modport_direction(other) == "input"
        and reads.intersection(other.outputs)
        for other in iface.modports
    )


@dataclass(frozen=True)
class InterfaceBinding:
    """What a port's interface type and modport say about its traffic.

    ``direction`` is :func:`modport_direction` (``None`` when the
    modport is missing, unparsed, or a tie). ``members`` maps each
    signal the modport exposes to its declared direction, sorted by
    name. ``observer`` is :func:`is_observer_modport`.
    """

    direction: Literal["input", "output"] | None
    members: tuple[tuple[str, _Direction], ...] = ()
    observer: bool = False


def interface_binding(port: Port, table: ModuleTable) -> InterfaceBinding | None:
    """Resolve an interface port's modport, or ``None`` for a wire port.

    An interface port whose type the filelist doesn't define, or which
    names no modport (``bus_if b``), or a modport the interface
    doesn't declare, resolves to an empty binding: no direction, no
    members.
    """
    port = resolve_interface_port(port, table)
    if port.port_kind != "interface":
        return None
    iface = table.interfaces_by_name.get(port.interface_type or "")
    if iface is None or port.modport is None:
        return InterfaceBinding(direction=None)
    modport = next((mp for mp in iface.modports if mp.name == port.modport), None)
    if modport is None:
        return InterfaceBinding(direction=None)
    members: dict[str, _Direction] = {}
    for direction, names in (
        ("input", modport.inputs),
        ("output", modport.outputs),
        ("inout", modport.inouts),
    ):
        for name in names:
            members.setdefault(name, direction)  # type: ignore[arg-type]
    return InterfaceBinding(
        direction=modport_direction(modport),
        members=tuple(sorted(members.items())),
        observer=is_observer_modport(iface, modport),
    )


def port_direction(port: Port, table: ModuleTable) -> _Direction | None:
    """A port's declared direction, or its modport's folded one.

    A wire port reports what it declares. An interface port has no
    declared direction; its modport's (:func:`modport_direction`) is
    the best one-word answer there is, and ``None`` when the modport
    can't decide.
    """
    if port.direction is not None:
        return port.direction
    binding = interface_binding(port, table)
    return binding.direction if binding is not None else None


def boundary_directions(
    module: Module, table: ModuleTable
) -> dict[str, _Direction | None]:
    """Which way each of ``module``'s ports faces, seen from outside.

    The direction a block diagram's boundary flag and an ELK root pin
    need (rtl-buddy-sch#195):

    * a wire port: what it declares (``None`` when it declares
      nothing);
    * an interface port with a modport: the modport's folded direction
      (:func:`modport_direction`) — ``bus_if.src out_bus`` is an
      ``"output"``;
    * an interface port with no modport (``bus_if gen``), or one whose
      modport ties: **the child modports it binds to**. ``gen`` bound
      to a ``snk`` feeds that child, so it is an ``"input"``; bound to
      a ``src`` it is an ``"output"``. Whole bindings vote first; with
      none, member selects (``gen.a_en``) vote by their pins'
      directions under the same fold. ``None`` when that is still
      undecided — the caller draws a bidirectional flag.
    """
    module = _resolved_ports(module, table)
    return _boundary_directions(module, table, None)


def _boundary_directions(
    module: Module, table: ModuleTable, pins: tuple[_Pin, ...] | None
) -> dict[str, _Direction | None]:
    """:func:`boundary_directions` over already-resolved ports / pins."""
    out: dict[str, _Direction | None] = {}
    for port in module.ports:
        direction = port_direction(port, table)
        if direction is None and port.port_kind == "interface":
            if pins is None:
                interfaces = interface_roots(module, table)
                pins = _collect_pins(
                    module,
                    table,
                    None,
                    interfaces,
                    _interface_modports(module, table),
                    _scope_variables(module, interfaces),
                )
            direction = _direction_from_children(port.name, pins)
        out[port.name] = direction
    return out


def _direction_from_children(
    name: str, pins: tuple[_Pin, ...]
) -> Literal["input", "output"] | None:
    """A scope interface port's direction, read off the pins bound to it.

    A child pin that reads the bus (``"input"``) is fed by the port, so
    the port is an input of the scope, and the other way round.
    """
    whole = [("", p.direction) for p in pins if name in p.roots and p.direction]
    if whole:
        return _fold_votes(whole)
    members = [
        (root.rsplit(".", 1)[-1], p.direction)
        for p in pins
        if p.direction
        for root in p.roots
        if _is_member_key(root) and _key_root(root) == name
    ]
    return _fold_votes(members)


def port_width(
    type_text: str | None, params: Mapping[str, str] | None = None
) -> int | None:
    """Declared bit width of a port from its verbatim type slice.

    ``logic [7:0]`` → 8, ``logic`` → 1, ``logic [PTR_W-1:0]`` → None,
    ``None`` → None::

        port_width("logic [18:0]")                     == 19
        port_width("wire")                             == 1
        port_width("logic [W-1:0]")                    is None
        port_width("logic [W-1:0]", {"W": "19"})       == 19
        port_width("logic [W-1:0]", {"W": "PTR_W"})    is None

    ``params`` maps parameter names to their **verbatim value text**
    at this binding site (see :func:`instance_params`). When the
    literal range parse fails, the bounds are re-read with those
    values substituted and folded by
    :func:`rtl_buddy_view.width_expr.eval_int_expr` — integers,
    ``+ - *``, parentheses and unary minus only. A ``$clog2`` call, a
    division, or a parameter that resolves to another symbol folds to
    nothing and the answer stays *unknown*, because a wrong bus width
    on a schematic is worse than an unlabelled wire.

    :func:`port_width_expr` is the other, independent answer, computed
    from the same slice without substitution. Both are always
    computed; neither suppresses the other.

    The literal parse runs first and unchanged, so no width that
    resolved before ``params`` existed can move. Only the leading
    packed range is read: for ``logic [3:0][7:0]`` (a packed array)
    the first dimension is returned, which is the number of elements,
    not the bit count — a documented under-report rather than a silent
    wrong total.

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
            return width_of(type_text, params)
        msb, lsb = int(match.group(1)), int(match.group(2))
        return abs(msb - lsb) + 1
    if not type_text.strip():
        return None
    return 1


def port_width_expr(type_text: str | None) -> str | None:
    """Algebraic bit width of a port, read off the type **as written**.

    The companion to :func:`port_width`, and deliberately *not*
    subordinate to it — the two are independent answers and a port may
    carry both::

        port_width_expr("logic [7:0]")            is None
        port_width_expr("logic [PTR_W-1:0]")      == "PTR_W"
        port_width_expr("logic [WIDTH-1:0]")      == "WIDTH"   # even
                                                   # under #(.WIDTH(19)),
                                                   # where port_width is 19
        port_width_expr("logic [$clog2(D)-1:0]")  is None

    No ``params`` argument, on purpose: substituting would replace the
    name the designer wrote with the caller's name or with one
    instantiation's number, and the name is the point (see
    :func:`rtl_buddy_view.width_expr.width_expr_of`). Only the one
    clean pattern documented on
    :func:`rtl_buddy_view.width_expr.simplify_range` produces an
    answer; everything else is silence.
    """
    if type_text is None or "[" not in type_text:
        return None
    if _PACKED_RANGE_RE.search(type_text) is not None:
        return None  # a literal range — there is no algebra in it
    return width_expr_of(type_text)


def module_param_defaults(module: Module | None) -> dict[str, str]:
    """``name -> default value text`` for a module's own parameters.

    The floor every width substitution starts from: what the module
    says its parameters are when nobody overrides them.
    """
    if module is None:
        return {}
    return {p.name: p.default_text for p in module.parameters if p.default_text}


def instance_params(
    child: Module | None, overrides: tuple[ParameterOverride, ...] = ()
) -> dict[str, str]:
    """Effective parameter values at one binding site.

    The child's declared defaults, with ``#(.WIDTH(19))`` written over
    them. A positional override is resolved against the child's
    declared parameter order — the same resolution
    ``elk_export._param_overrides`` makes for its label, and safe here
    for the same reason: an index into a declaration we *have* is not
    a guess. A positional override on an unknown child is dropped.

    Values stay verbatim source text (``"19"``, ``"PTR_W"``,
    ``"$clog2(DEPTH)"``); deciding which of those is a number is
    :mod:`rtl_buddy_view.width_expr`'s job, not this one's.
    """
    params = module_param_defaults(child)
    declared = [p.name for p in child.parameters] if child is not None else []
    for index, override in enumerate(overrides):
        name = override.param_name
        if name is None:
            if index >= len(declared):
                continue
            name = declared[index]
        params[name] = override.value_text
    return params


def scope_connectivity(
    module: Module,
    table: ModuleTable,
    *,
    params: Mapping[str, str] | None = None,
    net_hints: Mapping[str, NetHints] | None = None,
    pin_hints: Mapping[str, Mapping[str, PinDirection]] | None = None,
) -> tuple[NetEdge, ...]:
    """Compute net-level dataflow among ``module``'s direct children.

    Endpoints are ``module``'s own ports and its direct child
    instances — nothing deeper. Call it once per scope to build a
    nested block diagram.

    ``params`` is the effective parameter map of the *instance* this
    scope is being drawn for (:func:`instance_params`), used to width
    the scope's own nets and ports. It matters whenever a module is
    instantiated with an override: ``ip_async_fifo``'s ``DATA_W``
    defaults to 8 and the demo binds it to 19, so a caller that knows
    the binding site must say so or every wire inside that instance is
    labelled with the wrong number. Omit it and the module's own
    defaults are used, which is the best a per-module caller (the
    ``--block-diagram`` dot renderer) can do.

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
    * A member select on an interface (:func:`interface_roots`) is its
      own net, keyed and labelled by its member path (``cfg.a_en``,
      ``q[1].c_sta``; :func:`net_keys`). A binding of the *whole*
      interface (``.bus(cfg)``) stays on the interface's name and also
      joins every member net the scope selects, so it keeps the edges
      it had before members were split; so does a scope interface
      port. Net hints on the interface apply to all its members.
    * A child pin whose formal is an interface port with a **modport**
      (``bus_if.src o``) takes the modport's folded direction
      (:func:`modport_direction`): ``.o(shared)`` drives ``shared``
      and ``.i(shared)`` on a ``snk`` reads it, so the two siblings get
      one edge labelled ``shared`` (rtl-buddy-sch#194). Its member
      nets, where the scope selects any, take the modport's
      per-signal directions instead. A modport-less (or undecidable)
      interface formal faces the known pins: it reads when the group
      has a driver and drives when the group has only readers. An
      edge whose every destination pin is a passive monitor modport
      (:func:`is_observer_modport`) is ``side`` emphasis unless a
      hint says otherwise. Interface *instances* are not endpoints
      at all: they are the wire.
    * A scope's own interface port has no direction, so it joins its
      group as a driver when the group has known sinks and as a sink
      when it has known drivers — both when it has both, since an
      interface bundle genuinely carries traffic in both directions.
      With neither it defaults to a driver (an unconsumed bundle still
      reads as an input to the block).
    * A driver only reaches a sink whose root is reachable from the
      driver's root along the assign flow graph — continuous and
      ``always*`` assignments alike (reflexively, so a
      shared net needs no assign at all). A pin driving the *left*
      side of an assign is therefore not a driver of anything the RHS
      feeds — that direction would be a second driver on the net.
    * Groups whose every net name looks like a clock or a reset are
      dropped wholesale. "Group" here is a weakly connected component
      of the flow graph, so the filter sees exactly the net set an
      undirected alias model would have built.

    ``net_hints`` is this scope's slice of the ``rbsch`` net
    vocabulary (:meth:`rtl_buddy_view.hints.HintMap.nets_for_module`),
    keyed by net name. A ``classification`` overrides the name-regex
    clock/reset filter in both directions — a net classified
    ``"clock"``/``"reset"`` counts as clock-like whatever it is
    called, and one classified ``"data"`` (or carrying any
    ``emphasis``) keeps its group in the dataflow even when every
    name in it looks like a clock. An ``emphasis`` lands on the
    resulting edge's :attr:`NetEdge.emphasis`, and a ``bundle`` names
    the edge when every net on it agrees (with an opposite-direction
    edge of the same name folded into the data direction). ``None``
    (the default) is byte-identical to before the parameter existed.

    ``pin_hints`` (phase 5) recovers pin directions for children the
    table cannot resolve — vendor blackboxes with no port list.
    Keyed instance name → formal pin → direction
    (:meth:`rtl_buddy_view.hints.HintMap.pin_directions_for`). A hint
    is consulted only where resolution came up empty: a resolved
    module's own declaration always wins, because the RTL is the
    authority and a stale hint must not be able to flip a real
    direction.

    Returned edges are bundled per endpoint pair and sorted by
    ``(src, dst)``. Each carries the formal pin names it attaches to
    (``src_pins`` / ``dst_pins``) and, when every net's width is
    knowable, the bundle's total ``bits`` (or ``bits_expr`` when the
    answer is algebraic) — all captured here, where the binding site
    is still in hand, rather than re-derived by a renderer
    intersecting net names after the fact. Widths come from the ranked
    sources :func:`_net_width_info` documents, so a net that only an
    ``assign`` drives is still measured, from its own declaration.
    """
    scope_params = params if params is not None else module_param_defaults(module)
    module = _resolved_ports(module, table)
    interfaces = interface_roots(module, table)
    variables = _scope_variables(module, interfaces)
    raw_pins = _collect_pins(
        module,
        table,
        pin_hints,
        interfaces,
        _interface_modports(module, table),
        variables,
    )
    assign_refs = [
        (
            _scan_refs(a.lhs_text, interfaces, None, variables),
            _scan_refs(a.rhs_text, interfaces, None, variables),
        )
        for a in (*module.assigns, *module.procedural_assigns)
    ]
    members = _members_by_root(
        [key for pin in raw_pins for key in pin.roots]
        + [key for lhs, rhs in assign_refs for key in (*lhs[0], *rhs[0])]
    )
    pins = tuple(split for pin in raw_pins for split in _expand_whole(pin, members))
    # A whole reference on either side of an assign stands on every
    # element / member key it spans too, exactly like a whole pin.
    assign_keys = [
        (_spanned_keys(*lhs, members), _spanned_keys(*rhs, members))
        for lhs, rhs in assign_refs
    ]
    # Pins first so their roots exist as flow-graph nodes before the
    # assign pass wires them; isolated roots still need a component of
    # their own for the direct same-net case.
    flow = _FlowGraph()
    for pin in pins:
        for root in pin.roots:
            flow.add(root)
    for port in module.ports:
        for key in _port_keys(port, members):
            flow.add(key)
    for lhs_roots, rhs_roots in assign_keys:
        for lhs in lhs_roots:
            for rhs in rhs_roots:
                flow.connect(rhs, lhs)

    groups = _build_groups(
        module,
        pins,
        flow,
        members,
        _boundary_directions(module, table, raw_pins),
        table,
    )
    widths, exprs = _net_width_info(module, pins, scope_params)

    # (src, dst) -> (net names, src formals, dst formals), accumulated
    # across every group that produced the pair so a bundle carries
    # all of its signals and all of the pins they land on.
    bundled: dict[tuple[Endpoint, Endpoint], tuple[set[str], set[str], set[str]]] = {}
    for group in groups.values():
        if _is_clock_or_reset_group(group.names, net_hints):
            continue
        for src, dst, nets, src_pins, dst_pins in group.edges(flow):
            slot = bundled.setdefault((src, dst), (set(), set(), set()))
            slot[0].update(nets)
            slot[1].update(src_pins)
            slot[2].update(dst_pins)

    observers = {(pin.endpoint, pin.formal) for pin in pins if pin.observer}
    for nets, _, _ in bundled.values():
        nets -= _covered_elements(nets)
    edges = [
        NetEdge(
            src=src,
            dst=dst,
            nets=tuple(sorted(nets)),
            src_pins=tuple(sorted(src_pins)),
            dst_pins=tuple(sorted(dst_pins)),
            bits=_bundle_bits(nets, widths),
            bits_expr=_bundle_bits_expr(nets, widths, exprs),
            emphasis=_bundle_emphasis(nets, net_hints)
            or _observer_emphasis(dst, dst_pins, observers),
            bundle=_bundle_name(nets, net_hints),
        )
        for (src, dst), (nets, src_pins, dst_pins) in sorted(bundled.items())
    ]
    if net_hints and any(h.bundle for h in net_hints.values()):
        edges = _fold_bundle_returns(edges)
    return tuple(edges)


# --- internals ---------------------------------------------------------------


def _spanned_keys(
    keys: tuple[str, ...],
    whole: tuple[str, ...],
    members: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """``keys`` plus every member / element key a whole reference spans."""
    out = dict.fromkeys(keys)
    for prefix in whole:
        out.update(dict.fromkeys(_members_under(prefix, members)))
    return tuple(out)


def _covered_elements(nets: set[str]) -> set[str]:
    """Vector element keys on an edge that also carries the whole vector.

    A whole-vector driver reaching a whole-vector reader reaches every
    element key on the way (:func:`_expand_whole`); the label says
    ``dv`` once rather than ``dv, dv[0], dv[1]``, and the width is the
    vector's rather than a double count.
    """
    return {n for n in nets if _is_element_key(n) and _key_root(n) in nets}


@dataclass(frozen=True)
class _Pin:
    """One resolved child-instance pin: where it lands and which way.

    ``formal`` is the child's own port name (the ``.q`` of ``.q(w)``)
    — the schematic-facing identity that survives into
    :attr:`NetEdge.src_pins`. ``type_text`` is that port's declared
    type slice, kept for :func:`port_width`, and ``params`` is the
    child instance's effective parameter map, which is what turns
    ``logic [WIDTH-1:0]`` into a number (or into ``PTR_W``). Stored as
    a sorted tuple of pairs rather than a dict so the frozen dataclass
    stays hashable and comparable.
    """

    endpoint: Endpoint
    direction: _Direction | None
    roots: tuple[str, ...]
    formal: str
    type_text: str | None = None
    params: tuple[tuple[str, str], ...] = ()
    #: Whole-interface references in the binding, as select prefixes
    #: (``cfg``, ``q[1]``) — see :func:`_expand_whole`.
    whole: tuple[str, ...] = ()
    #: The formal is an interface port (rtl-buddy-sch#194). Its
    #: direction, when set, is the modport's folded one, and it lends
    #: no width: an interface bundle has none.
    iface: bool = False
    #: An interface formal whose direction the modport could not
    #: settle (no modport, an unparsed one, or a tie). Joins its group
    #: opposite to the known pins — see :meth:`_Group.edges`.
    generic: bool = False
    #: The modport's per-signal directions, for splitting a whole
    #: binding onto the member nets the scope selects.
    modport_signals: tuple[tuple[str, _Direction], ...] = ()
    #: A passive-monitor modport (:func:`is_observer_modport`).
    observer: bool = False


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

    Built once from the scope's assignments, then queried; the
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
        self.generic: _Bucket = {}

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
            _merge_bucket(sinks, self.generic)
        elif sinks:
            # A modport-less interface pin facing only readers is
            # their source: it takes the direction opposite to the
            # modport pins it meets (rtl-buddy-sch#194). Facing
            # drivers it reads, like any unknown pin; facing nothing
            # known it stays undrawn rather than invent an arrow.
            _merge_bucket(drivers, self.generic)
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


def _collect_pins(
    module: Module,
    table: ModuleTable,
    pin_hints: Mapping[str, Mapping[str, PinDirection]] | None = None,
    interfaces: frozenset[str] = frozenset(),
    modports: Mapping[str, frozenset[str]] | None = None,
    variables: frozenset[str] = frozenset(),
) -> tuple[_Pin, ...]:
    """Resolve every named child-instance pin into a :class:`_Pin`.

    ``pin_hints`` fills directions only where resolution came up
    empty (an unresolved child, or a pin the child's port list
    doesn't know) — the declaration always beats the hint.
    ``interfaces`` names the scope's interfaces, whose member selects
    key on the member path (:func:`net_keys`); ``modports`` lets a
    modport select (``.m(u_if.sub)``) bind the whole interface;
    ``variables`` (:func:`_scope_variables`) decides which vector
    indices are constant.
    """
    pins: list[_Pin] = []
    for inst in module.instances:
        if inst.module_name in table.interfaces_by_name:
            # An interface instance is the wire, not an endpoint: its
            # own port list (``.clk(clk)``) feeds the bundle, and the
            # block diagram draws no box for it (rtl-buddy-sch#194).
            continue
        hinted: Mapping[str, PinDirection] = (
            pin_hints.get(inst.name, {}) if pin_hints is not None else {}
        )
        child = table.modules_by_name.get(inst.module_name)
        if child is not None:
            child = _resolved_ports(child, table)
        directions: dict[str, _Direction | None] = (
            {p.name: p.direction for p in child.ports} if child is not None else {}
        )
        types: dict[str, str | None] = (
            {p.name: p.type_text for p in child.ports if p.port_kind == "wire"}
            if child is not None
            else {}
        )
        bindings: dict[str, InterfaceBinding] = {}
        if child is not None:
            for p in child.ports:
                binding = interface_binding(p, table)
                if binding is not None:
                    bindings[p.name] = binding
        params = tuple(sorted(instance_params(child, inst.param_overrides).items()))
        endpoint: Endpoint = ("inst", inst.name)
        for conn in inst.port_connections:
            if conn.port_name is None:
                # Positional — the child's port order is not reliable
                # enough to bind a name to. See scope_connectivity.
                continue
            # Implicit ``.name`` shorthand: the elaborator binds it to
            # the same-named net, so that is what we resolve it to.
            expr = conn.net_expr_text.strip() or conn.port_name
            roots, whole = _scan_refs(expr, interfaces, modports, variables)
            if not roots:
                continue
            direction = directions.get(conn.port_name) if child is not None else None
            binding = bindings.get(conn.port_name)
            if binding is not None:
                direction = binding.direction
            elif direction is None:
                direction = hinted.get(conn.port_name)
            pins.append(
                _Pin(
                    endpoint=endpoint,
                    direction=direction,
                    roots=roots,
                    formal=conn.port_name,
                    type_text=types.get(conn.port_name),
                    params=params,
                    whole=whole,
                    iface=binding is not None,
                    generic=binding is not None and binding.direction is None,
                    modport_signals=binding.members if binding is not None else (),
                    observer=binding is not None and binding.observer,
                )
            )
    return tuple(pins)


def _members_by_root(keys: list[str]) -> dict[str, tuple[str, ...]]:
    """Interface member and vector element keys, grouped by root, sorted."""
    found: dict[str, set[str]] = {}
    for key in keys:
        if _is_select_key(key):
            found.setdefault(_key_root(key), set()).add(key)
    return {root: tuple(sorted(under)) for root, under in found.items()}


def _members_under(prefix: str, members: Mapping[str, tuple[str, ...]]) -> list[str]:
    """The member keys a whole-interface select prefix spans.

    ``cfg`` spans every ``cfg.*`` member and ``q`` every element's
    members; ``q[1]`` only ``q[1].*``. A vector's whole reference
    (``dv``) spans every element key (``dv[SR_A]``).
    """
    root = _key_root(prefix)
    under = members.get(root, ())
    if prefix == root:
        return list(under)
    # A generate-scoped interface (``g[0].sub``) is not its key's root
    # (``g``), so its elements' members (``g[0].sub[1].v``) are under
    # ``prefix[``, not only ``prefix.``.
    return [key for key in under if key.startswith((prefix + ".", prefix + "["))]


def _expand_whole(
    pin: _Pin, members: Mapping[str, tuple[str, ...]]
) -> tuple[_Pin, ...]:
    """Attach a whole-interface binding to the member nets it spans.

    A pin bound to a whole interface (``.bus(cfg)``) keeps its root key
    — two whole bindings of one interface meet on ``cfg``, oriented by
    their modports' folded directions — and additionally joins every
    member net of that interface the scope references by member
    select. Without this, a whole binding would lose the edges it had
    before member selects got their own keys.

    When the formal's modport is known, each member joins in **the
    direction the modport gives that member** (rtl-buddy-sch#194): a
    ``snk`` binding drives ``cfg.r`` and reads ``cfg.v``, whatever the
    folded direction of the whole says. A member the modport doesn't
    expose is not reachable through this binding and is skipped. A
    modport-less binding joins every member with the pin's own
    (unresolved) direction, as before.

    A whole **vector** reference (``.q(dv)``, or a select that fell
    back to the vector, ``.d(dv[sel])``) spans the element keys the
    scope selects (``dv[SR_A]``) the same way (rtl-buddy-sch#208), so a
    whole-vector driver still reaches an element reader and the other
    way round, while two elements stay apart.

    Spanned keys join as pins of their own carrying no type: the
    formal's declared width is the whole binding's, not one member's
    or element's.
    """
    if not pin.whole:
        return (pin,)
    spanned: dict[str, None] = {}
    for prefix in pin.whole:
        spanned.update(dict.fromkeys(_members_under(prefix, members)))
    if not pin.modport_signals:
        extra = [key for key in spanned if key not in pin.roots]
        return (
            pin,
            *(
                dataclasses.replace(pin, roots=(key,), type_text=None, whole=())
                for key in extra
            ),
        )
    by_signal = dict(pin.modport_signals)
    out = [pin]
    for key in spanned:
        direction = by_signal.get(key.rsplit(".", 1)[-1])
        if direction is None:
            continue
        out.append(
            dataclasses.replace(
                pin, roots=(key,), direction=direction, generic=False, whole=()
            )
        )
    return tuple(out)


def _port_keys(port: Port, members: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """The net keys a scope port stands on.

    A port is its own net. An interface port is the root of its
    members, and a vector port the root of its elements, so — like a
    whole-interface pin — it stands on its own name *and* on every
    member or element of it the scope selects.
    """
    return (port.name, *members.get(port.name, ()))


def _net_width_info(
    module: Module, pins: tuple[_Pin, ...], params: Mapping[str, str]
) -> tuple[dict[str, int], dict[str, str]]:
    """Width of each net root, resolved through three ranked sources.

    Returns two maps: the numeric widths, and the algebraic ones for
    the nets that only have a symbolic answer. Both are filled by the
    same three tiers with the same abstention rules — a net's number
    and its expression are two readings of one declaration, so they
    cannot be resolved from different sources.

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
    widths: dict[str, int] = {}
    exprs: dict[str, str] = {}
    for tier_widths, tier_exprs in (
        _driver_widths(module, pins, params),
        _decl_widths(module, params),
        _port_widths(module, params),
    ):
        for root, width in tier_widths.items():
            widths.setdefault(root, width)
        for root, expr in tier_exprs.items():
            exprs.setdefault(root, expr)
    return widths, exprs


#: One tier's answer: ``(numeric widths, algebraic widths)``.
_Tier = tuple[dict[str, int], dict[str, str]]

_Width = TypeVar("_Width", int, str)


def _resolve(candidates: dict[str, set[_Width | None]]) -> dict[str, _Width]:
    """Keep only the names one tier pinned to exactly one known width."""
    return {
        root: next(iter(widths))  # type: ignore[misc]
        for root, widths in candidates.items()
        if len(widths) == 1 and None not in widths
    }


class _Candidates:
    """A tier's evidence, numeric and algebraic, gathered side by side.

    Every ``record`` call adds one reading of one name in this tier;
    :meth:`resolved` then applies the abstention rule (exactly one
    known value, no unknowns) to each map independently.
    """

    def __init__(self) -> None:
        self.widths: dict[str, set[int | None]] = {}
        self.exprs: dict[str, set[str | None]] = {}

    def record(
        self,
        name: str,
        type_text: str | None,
        params: Mapping[str, str] | None,
        *,
        known: bool = True,
    ) -> None:
        """Record what ``type_text`` says about ``name``.

        Both answers, always: the number (with ``params`` substituted)
        and the expression (as written). They are independent, so a
        net may resolve to 19 *and* to ``"WIDTH"``.

        ``known=False`` records the *absence* of an answer — an
        interface bundle, a multi-root binding — which is what makes
        the tier abstain rather than resolve from its other evidence.
        """
        width = port_width(type_text, params) if known else None
        expr = port_width_expr(type_text) if known else None
        self.widths.setdefault(name, set()).add(width)
        self.exprs.setdefault(name, set()).add(expr)

    def resolved(self) -> _Tier:
        return _resolve(self.widths), _resolve(self.exprs)


def _driver_widths(
    module: Module, pins: tuple[_Pin, ...], params: Mapping[str, str]
) -> _Tier:
    """Tier 1 — widths declared by whatever drives the net.

    A pin is read with the *child instance's* parameters (``.WIDTH(19)``
    at that binding site), a scope port with the scope's own.
    """
    candidates = _Candidates()
    for pin in pins:
        if pin.direction not in ("output", "inout") or pin.iface:
            # An interface formal has no width to lend, and its folded
            # direction must not make a member net's other, real
            # driver look contested.
            continue
        if len(pin.roots) != 1:
            for root in pin.roots:
                candidates.record(root, None, None, known=False)
            continue
        candidates.record(pin.roots[0], pin.type_text, dict(pin.params))
    for port in module.ports:
        if port.direction != "input":
            continue
        candidates.record(
            port.name, port.type_text, params, known=port.port_kind == "wire"
        )
    return candidates.resolved()


def _decl_widths(module: Module, params: Mapping[str, str]) -> _Tier:
    """Tier 2 — widths from the scope's own net/variable declarations."""
    candidates = _Candidates()
    for decl in module.net_decls:
        candidates.record(decl.name, decl.type_text, params)
    return candidates.resolved()


def _port_widths(module: Module, params: Mapping[str, str]) -> _Tier:
    """Tier 3 — widths from the scope's own port declarations."""
    candidates = _Candidates()
    for port in module.ports:
        candidates.record(
            port.name, port.type_text, params, known=port.port_kind == "wire"
        )
    return candidates.resolved()


def _bundle_bits(nets: set[str], widths: dict[str, int]) -> int | None:
    """Total width of a bundle, or ``None`` when any net is unknown."""
    total = 0
    for net in nets:
        width = widths.get(net)
        if width is None:
            return None
        total += width
    return total


def _bundle_bits_expr(
    nets: set[str], widths: dict[str, int], exprs: dict[str, str]
) -> str | None:
    """Algebraic total of a bundle, or ``None``.

    Each net contributes **its symbolic term when it has one** — a net
    that is both 19 bits and ``WIDTH``-wide contributes ``WIDTH``, and
    is deliberately *not* also counted in the numeric remainder. Nets
    with no expression contribute their number. One term plus the
    remainder prints (``"WIDTH+1"``); two terms, an all-numeric bundle
    (``bits`` says it better), or a net that is unknown both ways
    abstains. See :func:`rtl_buddy_view.width_expr.sum_width_exprs` for
    why identical terms are not collected.

    Sorted iteration, like everything else here: the emitted string is
    part of the payload's bytes.
    """
    numeric = 0
    terms: list[str] = []
    for net in sorted(nets):
        expr = exprs.get(net)
        if expr is not None:
            terms.append(expr)
            continue
        width = widths.get(net)
        if width is None:
            return None
        numeric += width
    return sum_width_exprs(terms, numeric)


def _build_groups(
    module: Module,
    pins: tuple[_Pin, ...],
    flow: _FlowGraph,
    members: Mapping[str, tuple[str, ...]] | None = None,
    port_dirs: Mapping[str, _Direction | None] | None = None,
    table: ModuleTable | None = None,
) -> dict[str, _Group]:
    """Bucket pins and module ports into their flow components.

    A scope port joins as its :func:`boundary_directions` say (an
    ``"input"`` drives the scope's nets, an ``"output"`` reads them);
    an interface port's selected members take the modport's
    per-signal direction where it has one. Whatever stays undecided
    joins the bidirectional ``interfaces`` bucket.
    """
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
            elif pin.generic:
                bucket = group.generic
            else:
                bucket = group.unknown
            group.add(bucket, pin.endpoint, root, pin.formal)

    for port in module.ports:
        endpoint: Endpoint = ("port", port.name)
        whole = port_dirs.get(port.name) if port_dirs is not None else port.direction
        binding = interface_binding(port, table) if table is not None else None
        signals = dict(binding.members) if binding is not None else {}
        for key in _port_keys(port, members or {}):
            group = group_for(key)
            direction = whole
            if key != port.name and binding is not None:
                # An interface member takes its modport's direction; a
                # vector element is as the port declares.
                direction = signals.get(key.rsplit(".", 1)[-1])
            if direction == "input":
                bucket = group.drivers
            elif direction in ("output", "inout"):
                bucket = group.sinks
            else:
                bucket = group.interfaces
            # The scope's own port *is* the pin at that endpoint, so the
            # formal name is the port name.
            group.add(bucket, endpoint, key, port.name)

    return groups


def _is_clock_or_reset_group(
    names: set[str], net_hints: Mapping[str, NetHints] | None = None
) -> bool:
    """True when every net in the component reads as a clock/reset.

    A group with even one data-looking name is kept: an
    ``assign en = go & ~clk_locked;`` style alias should not take the
    whole group down with it.

    An ``rbsch`` classification hint beats the name regexes in both
    directions — ``"clock"``/``"reset"`` make a net clock-like
    whatever it is called, ``"data"`` makes it data. A net carrying
    an ``emphasis`` is data too: ``main`` and ``side`` are statements
    about a *dataflow* edge, so making them also spell ``data`` saves
    the author the second word on exactly the nets phase 2 exists
    for.
    """
    if not names:
        return True

    def clock_like(name: str) -> bool:
        hint = _hint_for(name, net_hints)
        if hint is not None:
            if hint.classification in ("clock", "reset"):
                return True
            if hint.classification == "data" or hint.emphasis is not None:
                return False
        # An interface member is classified by its own name: ``bus.clk``
        # is a clock, and ``cfg.a_en`` is data whatever ``cfg`` is. A
        # vector element by its vector's: ``s_rst_l[SR_A]`` is a reset.
        leaf = name.rsplit(".", 1)[-1].split("[", 1)[0]
        return bool(
            _CLOCK_NAME_RE.search(leaf)
            or _RESET_NAME_RE.search(leaf)
            or _BLOCK_EXTRA_CLOCK_RE.search(leaf)
            or _BLOCK_EXTRA_RESET_RE.search(leaf)
        )

    return all(clock_like(name) for name in names)


def _hint_for(name: str, net_hints: Mapping[str, NetHints] | None) -> NetHints | None:
    """The net hint governing one net key.

    A plain net looks itself up. An interface member (``cfg.a_en``,
    ``q[1].c_sta``) has no declaration of its own in the scope to carry
    a pragma, so it inherits the hint on its interface — the instance
    or interface port that declares ``cfg`` / ``q`` — unless a hint is
    keyed on the member path itself. A vector element (``dv[SR_A]``)
    inherits the hint on its vector the same way. That is what lets ``bundle=`` on
    an interface fold its members back into one named edge.
    """
    if not net_hints:
        return None
    hint = net_hints.get(name)
    if hint is None and _is_select_key(name):
        hint = net_hints.get(_key_root(name))
    return hint


def _bundle_emphasis(
    nets: set[str], net_hints: Mapping[str, NetHints] | None
) -> Literal["main", "side"] | None:
    """The emphasis a bundle inherits from its member nets.

    ``main`` beats ``side``: when one bundle carries both, the author
    called part of it the money path, and thinning the whole edge
    would hide that. No hinted member → ``None`` (drawn as today).
    """
    if not net_hints:
        return None
    found = {
        hint.emphasis
        for hint in (_hint_for(name, net_hints) for name in nets)
        if hint is not None and hint.emphasis is not None
    }
    if "main" in found:
        return "main"
    if "side" in found:
        return "side"
    return None


def _observer_emphasis(
    dst: Endpoint, dst_pins: set[str], observers: set[tuple[Endpoint, str]]
) -> Literal["side"] | None:
    """``"side"`` for an edge that only taps into monitor modports.

    A passive monitor (:func:`is_observer_modport`) reads the bus it
    sits on; the edge into it is real dataflow, so it is drawn, but it
    is not the path a reader follows, so it is drawn thin. Applied
    only where the author's hints said nothing — an explicit emphasis
    always wins.
    """
    if dst_pins and all((dst, pin) in observers for pin in dst_pins):
        return "side"
    return None


def _bundle_name(
    nets: set[str], net_hints: Mapping[str, NetHints] | None
) -> str | None:
    """The bundle an edge belongs to — all member nets, or nothing.

    An edge naming a bundle that also carries unrelated nets would
    claim more than the author said, so a mixed edge keeps its plain
    net-list identity. The sidecar's ``""`` revoke is falsy, so a
    revoked membership breaks the unanimity exactly like an unhinted
    net does.
    """
    if not net_hints or not nets:
        return None
    names = {
        hint.bundle if hint is not None else None
        for hint in (_hint_for(n, net_hints) for n in nets)
    }
    if len(names) != 1:
        return None
    (name,) = names
    return name or None


def _fold_bundle_returns(edges: list[NetEdge]) -> list[NetEdge]:
    """Fold a bundle's return path into its data direction.

    A valid/ready pair flows both ways between the same two
    endpoints; the author's ``bundle=`` says it is *one* interface,
    so the diagram should show one edge, pointing the way the data
    goes. For each endpoint pair with same-named bundle edges in both
    directions, the direction carrying more payload wins — compared
    in bits when both widths are known, in net count otherwise — and
    the loser's nets and pins merge into it (pins swap sides: the
    return path's source pins sit on the kept edge's destination).
    A tie abstains and keeps both edges: guessing the data direction
    would point an arrow the wrong way on exactly the symmetric
    interfaces where the reader can't tell either.

    ``bits`` / ``bits_expr`` / ``emphasis`` stay the kept
    direction's: the slash describes payload, not payload plus
    handshake, and an emphasis word on the data nets already speaks
    for the interface.
    """
    by_pair = {(e.src, e.dst): e for e in edges}
    dropped: set[tuple[Endpoint, Endpoint]] = set()
    merged: dict[tuple[Endpoint, Endpoint], NetEdge] = {}
    for edge in edges:
        pair = (edge.src, edge.dst)
        if edge.bundle is None or pair in dropped or pair in merged:
            continue
        reverse = by_pair.get((edge.dst, edge.src))
        if reverse is None or reverse.bundle != edge.bundle:
            continue
        keep, fold = _data_direction(edge, reverse)
        if keep is None or fold is None:
            continue
        merged[(keep.src, keep.dst)] = dataclasses.replace(
            keep,
            nets=tuple(sorted({*keep.nets, *fold.nets})),
            src_pins=tuple(sorted({*keep.src_pins, *fold.dst_pins})),
            dst_pins=tuple(sorted({*keep.dst_pins, *fold.src_pins})),
        )
        dropped.add((fold.src, fold.dst))
    return [
        merged.get((e.src, e.dst), e) for e in edges if (e.src, e.dst) not in dropped
    ]


def _data_direction(
    a: NetEdge, b: NetEdge
) -> tuple[NetEdge, NetEdge] | tuple[None, None]:
    """Order two opposite bundle edges as (data direction, return path)."""
    if a.bits is not None and b.bits is not None:
        if a.bits > b.bits:
            return a, b
        if b.bits > a.bits:
            return b, a
        return None, None
    if len(a.nets) > len(b.nets):
        return a, b
    if len(b.nets) > len(a.nets):
        return b, a
    return None, None
