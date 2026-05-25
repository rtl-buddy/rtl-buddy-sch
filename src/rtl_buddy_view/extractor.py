"""SV semantic data model + CST walker.

The frontend layer returns a list of :class:`Module` dataclasses;
everything downstream of this module (graph builder, renderers, query
API) works on these in-memory objects with no further parser
dependency. Source anchors (`(file, line, col)`) are attached to every
node so consumers can cite or jump to the originating SV source.

Phase 1 scope: ``module``, ``port``, ``parameter``, ``instance``,
``port connection`` (named and positional). Generate blocks and
parameterized-instance resolution are deferred to Phase 2's slang
fallback path — see [rtl-buddy/rtl-buddy-view#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SourceLocation:
    """File + 1-indexed line/column pair from the source CST.

    All fields after ``file`` are optional so consumers can opt into
    however much precision the frontend gave them. ``end_*`` fields are
    populated when the CST node carries a span rather than a point.
    """

    file: str
    start_line: int | None = None
    start_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


@dataclass(frozen=True)
class Port:
    name: str
    direction: Literal["input", "output", "inout"] | None
    # Verbatim source slice of the type (e.g. ``"logic [7:0]"``). We
    # don't try to parse type expressions in Phase 1 — the slice is
    # enough for diagram labels and LLM context.
    type_text: str | None
    location: SourceLocation | None
    # SystemVerilog "interface ports" (``test_mem_if.sub m``) are
    # bundles of signals rather than scalar wires. They have no
    # direction (interfaces are bidirectional by construction) and
    # cannot be value-badge-eligible in the wave overlay. Default
    # ``"wire"`` keeps existing producers + consumers untouched;
    # ``port_kind == "interface"`` is the only value that adds the
    # follow-up fields. (rtl-buddy-view#102)
    #
    # ``"interface_signal"`` is the per-signal flattening output of
    # :func:`flatten_interface_ports` (#105): a synthesized scalar
    # port carrying a real ``direction`` pinned by the source
    # interface's modport. ``interface_type`` and ``modport``
    # remain populated so the SPA can render "from <iface>.<mp>"
    # styling.
    port_kind: Literal["wire", "interface", "interface_signal"] = "wire"
    # Populated only when ``port_kind == "interface"``:
    #   - ``interface_type``: the interface name (``test_mem_if``).
    #   - ``modport``: the named modport from the ``.modport`` suffix
    #     (``sub``). May be ``None`` when the source declared a bare
    #     interface port without a modport (``test_mem_if m;``).
    interface_type: str | None = None
    modport: str | None = None


@dataclass(frozen=True)
class Parameter:
    name: str
    # Verbatim source slice of the default value expression
    # (e.g. ``"8"``, ``"`MAX_WIDTH"``). Same rationale as
    # :attr:`Port.type_text`.
    default_text: str | None
    location: SourceLocation | None


@dataclass(frozen=True)
class PortConnection:
    """A single ``.port_name(net_expr)`` (named) or positional binding.

    ``port_name`` is None for positional connections (the index in the
    parent :class:`Instance.port_connections` list is the positional
    index).
    """

    port_name: str | None
    # Verbatim source slice of the net expression. Phase 2 may upgrade
    # this to a parsed expression tree if/when the JSON renderer needs
    # structural info (e.g. for fanout overlays).
    net_expr_text: str
    location: SourceLocation | None


@dataclass(frozen=True)
class ParameterOverride:
    """A single ``.PARAM(value)`` (named) or positional parameter override."""

    param_name: str | None
    value_text: str
    location: SourceLocation | None


@dataclass(frozen=True)
class Instance:
    name: str
    module_name: str
    param_overrides: tuple[ParameterOverride, ...]
    port_connections: tuple[PortConnection, ...]
    location: SourceLocation | None


@dataclass(frozen=True)
class Module:
    name: str
    ports: tuple[Port, ...]
    parameters: tuple[Parameter, ...]
    instances: tuple[Instance, ...]
    location: SourceLocation | None
    # Cell of any documentation comment immediately preceding the
    # module declaration. Verible's CST preserves leading trivia, so
    # we capture it here for downstream consumers (LLM context, doc
    # generators). May be empty.
    leading_doc: str = ""


@dataclass(frozen=True)
class InterfaceSignal:
    """One signal declared inside a SystemVerilog interface body.

    Mirrors :class:`Port` but lives at the interface-body level —
    interface ports flatten down to one synthesized :class:`Port`
    per signal when a modport pins the direction. (#105.)
    """

    name: str
    type_text: str | None
    location: SourceLocation | None


@dataclass(frozen=True)
class Modport:
    """A named modport declaration inside an interface.

    Each tuple holds signal names (declared inside the interface
    body) grouped by direction. A signal may appear in only one of
    the three buckets per modport.
    """

    name: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    inouts: tuple[str, ...] = ()
    location: SourceLocation | None = None


@dataclass(frozen=True)
class Interface:
    """A SystemVerilog interface declaration. (#105.)

    Captured so that interface ports on instantiated modules can be
    flattened into per-signal :class:`Port`-shaped entries with
    synthesized directions (from the modport). When no matching
    interface entry exists (blackbox interface, parametric type the
    elaborator didn't simplify), consumers fall back to the bundle
    row from #102.
    """

    name: str
    parameters: tuple[Parameter, ...]
    signals: tuple[InterfaceSignal, ...]
    modports: tuple[Modport, ...]
    location: SourceLocation | None


def flatten_interface_ports(
    module: "Module", table: "ModuleTable | None"
) -> tuple[Port, ...]:
    """Expand interface ports on ``module`` into per-signal scalar ports.

    For each port with ``port_kind == "interface"``:

    * If ``table`` is None, or the interface type isn't in
      ``table.interfaces_by_name``, or the named modport isn't in
      that interface, or the modport has no signals → keep the
      original bundle port unchanged. This is the graceful-fallback
      contract from #102.
    * Otherwise → emit one synthesized :class:`Port` per signal in
      the modport. Direction comes from the modport's input/output/inout
      bucket; name is ``"{port.name}.{signal.name}"``; ``port_kind``
      is ``"interface_signal"`` so downstream renderers can apply
      "from interface" styling without confusing it with a bare wire.

    Non-interface ports pass through verbatim. Source order is
    preserved (interface signals slot in where the bundle port sat),
    so block-flow's input/output column layout stays predictable.
    (#105.)
    """
    if not module.ports:
        return module.ports
    interfaces_by_name = table.interfaces_by_name if table is not None else {}
    out: list[Port] = []
    for port in module.ports:
        if port.port_kind != "interface":
            out.append(port)
            continue
        iface = interfaces_by_name.get(port.interface_type or "")
        modport = None
        if iface is not None and port.modport is not None:
            modport = next(
                (mp for mp in iface.modports if mp.name == port.modport),
                None,
            )
        if iface is None or modport is None:
            # Unresolved: keep the bundle row (matches the #102 SPA
            # fallback). When the interface is in scope but the
            # modport name is missing we also can't pin directions —
            # treating it as a bundle is safer than guessing.
            out.append(port)
            continue
        signal_type: dict[str, str | None] = {
            s.name: s.type_text for s in iface.signals
        }
        flattened = _flattened_signals_from_modport(
            port=port, modport=modport, signal_type=signal_type
        )
        if not flattened:
            # Modport without any directional entries — same fallback
            # as an unresolved type. Keeps the bundle visible rather
            # than silently dropping the port.
            out.append(port)
            continue
        out.extend(flattened)
    return tuple(out)


def _flattened_signals_from_modport(
    *,
    port: Port,
    modport: "Modport",
    signal_type: dict[str, str | None],
) -> list[Port]:
    """Build the list of synthesized scalar ports for one modport.

    Helper extracted so :func:`flatten_interface_ports` stays linear
    and the per-direction emit can be reasoned about in isolation.
    """
    flat: list[Port] = []
    for direction, signals in (
        ("input", modport.inputs),
        ("output", modport.outputs),
        ("inout", modport.inouts),
    ):
        for sig_name in signals:
            flat.append(
                Port(
                    name=f"{port.name}.{sig_name}",
                    direction=direction,  # type: ignore[arg-type]
                    type_text=signal_type.get(sig_name),
                    location=port.location,
                    port_kind="interface_signal",
                    interface_type=port.interface_type,
                    modport=port.modport,
                )
            )
    return flat


@dataclass
class ModuleTable:
    """Result of running the frontend over a project's source files.

    Phase 1 returns one of these per ``parse_to_modules`` call. The
    ``modules_by_name`` mapping is what the graph builder consumes;
    ``unresolved`` carries module-name references the extractor saw
    but couldn't resolve to a definition (libraries, blackboxes,
    missing files) — kept around so renderers can mark those nodes
    explicitly rather than silently dropping them.
    """

    modules_by_name: dict[str, Module] = field(default_factory=dict)
    unresolved: set[str] = field(default_factory=set)
    # Populated when the frontend encountered ``interface`` declarations.
    # Keyed by interface name. Consumers (json_render, dot, tree,
    # mermaid) use this to flatten interface ports into per-signal
    # rows via :func:`flatten_interface_ports`. (#105.)
    interfaces_by_name: dict[str, Interface] = field(default_factory=dict)
