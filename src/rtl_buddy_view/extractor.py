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
