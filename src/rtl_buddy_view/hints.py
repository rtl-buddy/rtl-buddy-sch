"""In-source ``rbsch`` pragmas + the hint sidecar (epic #159, phase 1).

Inference has a ceiling: the extractor can see *what* a design
instantiates, never *what the author meant*. Which blocks are noise,
what a block should be called in a document, and which wrapper is an
implementation detail are intent, not structure. This module is where
that intent enters the analyzer.

Two equivalent sources produce one :class:`HintMap`:

- **In-source pragmas** — ``// rbsch: key key2="value"`` magic
  comments living next to the RTL, reviewed and refactored with it.
  All rtl-buddy in-source pragmas begin with ``rb``; this tool owns
  the ``rbsch`` prefix (siblings: ``rbcdc``, ``rbxeno``, …).
- **A JSON sidecar** loaded through the overlay registry
  (``--overlay hints=hints.json``) for vendor / generated IP whose
  source can't carry comments. The sidecar wins on conflict — it is
  the deliberate override.

Layering: :func:`scan_pragmas` takes *text*, not paths. The CLI already
resolves the filelist, so it reads the sources and passes them in; that
keeps the scanner a pure function and lets unit tests exercise
association without a Verible binary. The one I/O entry point is
:func:`load_hint_sidecar`, mirroring
:func:`rtl_buddy_view.annotations.load_domain_map`.

Phase 1 vocabulary is abstraction control plus semantic labels:
``leaf`` (module), ``collapse`` / ``hide`` (instance) and ``label``
(either). ``leaf``/``collapse``/``hide`` transform the *graph* via
:func:`apply_hints`, so every renderer — dot, tree, mermaid, JSON —
agrees on what exists; ``label`` rides along on
:attr:`rtl_buddy_view.graph.HierNode.display_label` for renderers that
know how to show it.

Phase 2 adds **net classification**: ``clock`` / ``reset`` / ``data``
override the name-regex classifier the block-diagram connectivity
analyzer uses to drop clock trees (in both directions — a clock named
``tick`` can be suppressed, a data net named ``clkd_result`` can be
rescued), and ``main`` / ``side`` set the emphasis a renderer draws
the net's edge with. Net hints never transform the hierarchy; they are
consumed by :func:`rtl_buddy_view.connectivity.scope_connectivity`
(classification, emphasis) and styled by the dot renderer
(emphasis).

Presentation words for integration tops (rtl-buddy-sch#196):
``params=`` (module / instance) narrows the parameter rows a box shows
via :attr:`rtl_buddy_view.graph.HierNode.display_params`; ``hide`` and
``bundle=`` on a root port reshape the block diagram's boundary flags;
and ``color=`` on a clock net pins its colour
(:mod:`rtl_buddy_view.clock_colors`).

Unknown vocabulary never raises. A typo'd key is collected as a warning
naming the known set (the self-correcting-typo pattern the overlay
registry uses for unknown overlay names) so a bad pragma degrades to
"no hint" rather than "no diagram".

Generate-scoped targets (rtl-buddy-sch#217): an instance or net inside
a generate block is named the way :mod:`rtl_buddy_view.generate`
unrolls it — ``g[0].u_x``, ``g.u_x``, or ``g[*].u_x`` for a loop whose
bounds don't fold — so a sidecar key splits on its *first* dot only
(:func:`parse_hint_key`), and ``*`` in the name half is a wildcard
(:func:`key_pattern`). Wildcard keys are expanded against the names the
elaborated design actually has by :func:`bind_hint_keys`, which is
also where a key that names nothing gets its warning.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, TypeVar, cast

from rtl_buddy_view.clock_colors import color_spelling_help, parse_color
from rtl_buddy_view.extractor import Module, ModuleTable
from rtl_buddy_view.generate import module_env, scoped_net_names
from rtl_buddy_view.graph import HierNode

#: Comment prefix this tool answers to. The ``rb`` stem is the
#: rtl-buddy-wide in-source pragma convention; the suffix names the
#: consuming tool so a design can carry ``rbcdc`` / ``rbxeno``
#: pragmas side by side without either tool guessing.
PRAGMA_PREFIX = "rbsch"

#: Sidecar schema version this loader emits/accepts. Same
#: major-must-match / minor-may-drift rule as the clock-domain map.
#: 1.3 (rtl-buddy-sch#217): ``instances`` / ``nets`` keys may name a
#: generate-scoped target (``top.g[0].u_x``) and carry ``*`` wildcards.
SIDECAR_SCHEMA_VERSION = "1.3"
SUPPORTED_SCHEMA_MAJOR = 1

#: Valueless pragma words that shape a *box* (module or instance).
KNOWN_FLAGS: tuple[str, ...] = ("collapse", "hide", "leaf")

#: Valueless pragma words that classify a *net* (a body declaration
#: or a port). ``clock`` / ``reset`` / ``data`` override the
#: name-regex clock-tree classifier in both directions; ``main`` /
#: ``side`` set the emphasis the net's dataflow edge is drawn with.
KNOWN_NET_FLAGS: tuple[str, ...] = ("clock", "data", "main", "reset", "side")

#: ``key=value`` pragma words (box vocabulary). ``label`` is valid on
#: modules and instances; ``rank`` and ``group`` (phase 4, layout)
#: only mean something on an instance — ``rank=<n>`` biases the
#: column order so left→right reads as pipeline order, and
#: ``group=<name>`` draws a dashed container around related sibling
#: instances that aren't a hierarchy level.
KNOWN_KEYS: tuple[str, ...] = ("group", "in", "label", "out", "params", "rank")

#: The box keys that only mean something on an instance.
INSTANCE_ONLY_KEYS: tuple[str, ...] = ("group", "rank")

#: Blackbox pin-direction keys (phase 5): ``in=`` / ``out=`` carry a
#: comma-separated pin-name list and recover connectivity for pins of
#: *unresolved* modules — vendor IP the table has no port list for.
#: Valid on an instance (this one placement) or a module (every
#: placement — the sidecar's natural home, since a blackbox has no
#: source to comment). Directions of resolved modules always win;
#: a hint on one is ignored.
PIN_DIRECTION_KEYS: tuple[str, ...] = ("in", "out")

#: ``key=value`` pragma words that target a net. ``bundle=<name>``
#: names the wire group a net belongs to: every net sharing a bundle
#: name between one pair of endpoints renders as a single named edge,
#: and a return path (valid/ready style) folds into the data
#: direction.
KNOWN_NET_KEYS: tuple[str, ...] = ("bundle", "color")

#: Flags that only mean something on a module declaration.
MODULE_ONLY_FLAGS: tuple[str, ...] = ("leaf",)

#: Flags that only mean something on an instance.
INSTANCE_ONLY_FLAGS: tuple[str, ...] = ("collapse", "hide")

#: The two families inside the net vocabulary. Words *within* one
#: family are mutually exclusive on a single pragma; the conflict is
#: resolved (warned and dropped) in :func:`resolve_hints`, where the
#: pragma's location is known for the message.
CLASSIFICATION_FLAGS: tuple[str, ...] = ("clock", "data", "reset")
EMPHASIS_FLAGS: tuple[str, ...] = ("main", "side")

#: What a net's ``classification`` may resolve to.
Classification = Literal["clock", "data", "reset"]

#: What a net's ``emphasis`` may resolve to.
Emphasis = Literal["main", "side"]

#: What a ``params=`` hint resolves to (rtl-buddy-sch#196): ``"all"``
#: shows every parameter override in the box, ``"none"`` shows none,
#: and a tuple of parameter names keeps just those (sorted; display
#: order stays the source's override order).
ParamsHint = Literal["all", "none"] | tuple[str, ...]

#: What a hinted blackbox pin may resolve to. Deliberately no
#: ``inout``: a hint exists to recover a *dataflow* direction, and a
#: bidirectional claim recovers nothing.
PinDirection = Literal["input", "output"]

#: The wildcard a sidecar key's name half may carry (rtl-buddy-sch#217).
WILDCARD = "*"


class HintsError(ValueError):
    """Raised when a hint sidecar can't be loaded.

    Only the sidecar path raises: an in-source pragma is *source
    code the user is editing*, so a malformed one warns and is
    skipped. A sidecar is an artefact deliberately pointed at on the
    command line, and a silently-ignored one is worse than a loud
    failure — same split :mod:`rtl_buddy_view.annotations` makes.
    """


# --- payload parsing ---------------------------------------------------------


@dataclass(frozen=True)
class PragmaPayload:
    """Parsed body of one ``// rbsch: …`` comment.

    ``flags`` and ``options`` are sorted so two pragmas spelling the
    same intent in a different order compare (and render) equal.
    ``warnings`` are location-free — :func:`scan_pragmas` prefixes
    ``file:line`` when it collects them.
    """

    flags: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()

    def option(self, key: str) -> str | None:
        """Value for ``key``, or ``None`` when the pragma didn't set it."""
        for name, value in self.options:
            if name == key:
                return value
        return None

    @property
    def is_empty(self) -> bool:
        return not self.flags and not self.options


def _unknown_word_warning(word: str) -> str:
    return (
        f"unknown {PRAGMA_PREFIX} key {word!r}; known flags: "
        f"{', '.join(sorted(KNOWN_FLAGS + KNOWN_NET_FLAGS))}; known keys: "
        f"{', '.join(f'{k}=…' for k in sorted(KNOWN_KEYS + KNOWN_NET_KEYS))}"
    )


def parse_pragma_payload(payload: str) -> PragmaPayload:
    """Parse the text after ``// rbsch:`` into flags + options.

    Tokenisation is :func:`shlex.split`, so ``label="CSR block"`` and
    ``label='CSR block'`` both yield the one token ``label=CSR block``
    and quoting rules match what an RTL author already knows from the
    shell.

    Every rejection path produces a warning rather than an exception:
    an unknown word, a value on a valueless flag, a flag word used
    where a value is required, an unterminated quote, an empty body.
    """
    try:
        tokens = shlex.split(payload)
    except ValueError as e:
        return PragmaPayload(
            warnings=(f"malformed {PRAGMA_PREFIX} pragma ({e}): {payload.strip()!r}",)
        )
    if not tokens:
        return PragmaPayload(
            warnings=(
                f"empty {PRAGMA_PREFIX} pragma; expected one of "
                f"{', '.join(KNOWN_FLAGS)} or "
                f"{', '.join(f'{k}=…' for k in KNOWN_KEYS)}",
            )
        )

    flags: set[str] = set()
    options: dict[str, str] = {}
    warnings: list[str] = []
    for token in tokens:
        key, sep, value = token.partition("=")
        if not sep:
            if token in KNOWN_FLAGS or token in KNOWN_NET_FLAGS:
                flags.add(token)
            elif token in KNOWN_KEYS or token in KNOWN_NET_KEYS:
                warnings.append(
                    f'{PRAGMA_PREFIX} key {token!r} needs a value (write {token}="…")'
                )
            else:
                warnings.append(_unknown_word_warning(token))
            continue
        if not key:
            warnings.append(f"{PRAGMA_PREFIX} token {token!r} has no key before '='")
        elif key in KNOWN_KEYS or key in KNOWN_NET_KEYS:
            # Last spelling wins; a repeated key in one comment is a
            # typo either way and the warning-free path keeps the
            # diagram rendering.
            options[key] = value
        elif key in KNOWN_FLAGS or key in KNOWN_NET_FLAGS:
            warnings.append(
                f"{PRAGMA_PREFIX} flag {key!r} takes no value; "
                f"write it bare (drop the '={value}')"
            )
        else:
            warnings.append(_unknown_word_warning(key))
    return PragmaPayload(
        flags=tuple(sorted(flags)),
        options=tuple(sorted(options.items())),
        warnings=tuple(warnings),
    )


# --- scanning ----------------------------------------------------------------


@dataclass(frozen=True)
class Pragma:
    """One ``// rbsch: …`` comment located in a source file.

    ``file`` is the exact string the frontend puts in
    :attr:`rtl_buddy_view.extractor.SourceLocation.file` (the
    filelist-resolved absolute path), so association is a dictionary
    hit rather than a path-normalisation guess.

    ``own_line`` distinguishes the two association rules: a trailing
    pragma binds to the declaration starting on its own line, a
    standalone one binds to the next declaration below it.
    """

    file: str
    line: int
    own_line: bool
    payload: PragmaPayload

    @property
    def flags(self) -> tuple[str, ...]:
        return self.payload.flags

    def option(self, key: str) -> str | None:
        return self.payload.option(key)


def _find_pragma(line: str) -> tuple[int, str] | None:
    """Locate a ``// rbsch:`` comment in ``line``.

    Returns ``(comment_start_index, payload)``. Only line comments
    are recognised in phase 1 — a ``/* rbsch: … */`` block comment is
    not a pragma, which keeps the scanner a one-pass regex-free walk
    and avoids taking a position on multi-line comment association.
    """
    idx = line.find("//")
    while idx != -1:
        rest = line[idx + 2 :].lstrip()
        if rest.startswith(PRAGMA_PREFIX):
            after = rest[len(PRAGMA_PREFIX) :].lstrip()
            if after.startswith(":"):
                return idx, after[1:]
        idx = line.find("//", idx + 2)
    return None


def scan_pragmas(sources: Mapping[str, str]) -> tuple[Pragma, ...]:
    """Scan file text for ``// rbsch:`` pragmas.

    ``sources`` maps *the path string the frontend recorded* to that
    file's text. The CLI builds it from the same resolved filelist it
    hands the frontend; tests build it literally.

    Pure by construction — no path in, no path opened. Output is
    sorted by ``(file, line)`` so downstream warning order is stable
    regardless of the caller's mapping order.
    """
    found: list[Pragma] = []
    for file in sorted(sources):
        for lineno, text in enumerate(sources[file].splitlines(), start=1):
            hit = _find_pragma(text)
            if hit is None:
                continue
            start, payload = hit
            found.append(
                Pragma(
                    file=file,
                    line=lineno,
                    own_line=not text[:start].strip(),
                    payload=parse_pragma_payload(payload),
                )
            )
    return tuple(found)


# --- resolution --------------------------------------------------------------


@dataclass(frozen=True)
class ModuleHints:
    """Hints attached to a module *definition*.

    Tri-state fields: ``None`` means "this source said nothing",
    which is what lets a sidecar override an in-source ``leaf`` with
    an explicit ``false`` instead of only ever adding hints.
    """

    leaf: bool | None = None
    label: str | None = None
    in_pins: tuple[str, ...] | None = None
    out_pins: tuple[str, ...] | None = None
    #: Which parameter overrides every placement's box shows
    #: (rtl-buddy-sch#196); an instance's own ``params=`` wins.
    params: ParamsHint | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.leaf is None
            and self.label is None
            and self.in_pins is None
            and self.out_pins is None
            and self.params is None
        )


@dataclass(frozen=True)
class InstanceHints:
    """Hints attached to one instantiation, keyed by (parent module, name).

    ``rank`` and ``group`` (phase 4) are renderer-facing layout
    hints: ``rank`` biases the instance's column so left→right reads
    as pipeline order, ``group`` wraps same-named siblings in a
    dashed container. ``group=""`` — reachable only through a
    sidecar — is the explicit revoke, treated as "no group" by every
    consumer (the truthiness check they share with ``bundle``).
    """

    collapse: bool | None = None
    hide: bool | None = None
    label: str | None = None
    rank: int | None = None
    group: str | None = None
    in_pins: tuple[str, ...] | None = None
    out_pins: tuple[str, ...] | None = None
    params: ParamsHint | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.collapse is None
            and self.hide is None
            and self.label is None
            and self.rank is None
            and self.group is None
            and self.in_pins is None
            and self.out_pins is None
            and self.params is None
        )


@dataclass(frozen=True)
class NetHints:
    """Hints attached to one net, keyed by ``(module, net name)``.

    "Net" is anything a wire in a scope can be named after: a body
    declaration (``logic [18:0] payload;``) or a port. Both carry a
    :class:`~rtl_buddy_view.extractor.SourceLocation`, so both take a
    pragma.

    ``classification`` overrides the connectivity analyzer's
    name-regex clock-tree filter — ``"clock"`` / ``"reset"`` suppress
    a net whose name doesn't look the part, ``"data"`` rescues one
    whose name wrongly does. ``emphasis`` is renderer-facing:
    ``"main"`` emboldens the edge carrying this net, ``"side"`` thins
    and grays it. ``None`` everywhere means "this source said
    nothing" — the same tri-state rule the box hints follow, so a
    sidecar can override an in-source word rather than only add.
    """

    classification: Classification | None = None
    emphasis: Emphasis | None = None
    #: Name of the wire group this net belongs to (phase 3). ``None``
    #: means unspecified; ``""`` — reachable only through a sidecar —
    #: is the explicit revoke, and every consumer treats it as "no
    #: bundle" (the truthiness check they all share).
    bundle: str | None = None
    #: ``hide`` on a **port** (rtl-buddy-sch#196): the block diagram
    #: draws no boundary flag for it, and no edge to one. Only the
    #: rendered root's ports have flags, so on any other net it is
    #: inert.
    hide: bool | None = None
    #: The colour of the clock this net names (rtl-buddy-sch#196),
    #: normalised by :func:`rtl_buddy_view.clock_colors.parse_color`.
    #: ``""`` — reachable only through a sidecar — is the explicit
    #: revoke.
    color: str | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.classification is None
            and self.emphasis is None
            and self.bundle is None
            and self.hide is None
            and self.color is None
        )


@dataclass(frozen=True)
class HintMap:
    """Resolved hints, keyed the way the graph layer looks them up.

    Modules are keyed by name; instances by ``(parent_module_name,
    instance_name)`` — the pair a :class:`HierNode` can produce from
    itself and its parent without a path-string convention, and the
    same pair the sidecar spells ``"parent.inst"``. Nets are keyed by
    ``(module_name, net_name)`` — a net name is meaningful only
    inside the module that declares it, and the sidecar spells the
    pair ``"module.net"``.

    The name half of an instance or net key may be generate-scoped
    (``g[0].u_x``) and, before :func:`bind_hint_keys` has run, may be a
    ``*`` wildcard (``g[*].u_x``); a bound map holds concrete names
    only.

    ``source`` names the sidecar a map was loaded from, so the binding
    warnings can say which file a dead key lives in. It takes no part
    in equality.
    """

    modules: Mapping[str, ModuleHints] = field(default_factory=dict)
    instances: Mapping[tuple[str, str], InstanceHints] = field(default_factory=dict)
    nets: Mapping[tuple[str, str], NetHints] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    source: str | None = field(default=None, compare=False)

    @property
    def is_empty(self) -> bool:
        """True when no hint would change any output.

        The graceful-degradation gate: an empty map must leave every
        renderer byte-identical to the no-hints case, so
        :func:`apply_hints` short-circuits on it. Warnings alone
        don't make a map non-empty.
        """
        return not self.modules and not self.instances and not self.nets

    def for_module(self, module_name: str) -> ModuleHints | None:
        return self.modules.get(module_name)

    def for_instance(
        self, parent_module: str, instance_name: str
    ) -> InstanceHints | None:
        return self.instances.get((parent_module, instance_name))

    def nets_for_module(self, module_name: str) -> dict[str, NetHints]:
        """The net hints scoped to one module, keyed by net name.

        This is the shape
        :func:`rtl_buddy_view.connectivity.scope_connectivity`
        consumes — that function analyzes exactly one scope, so it
        should never see (or filter) another module's nets.
        """
        return {net: h for (mod, net), h in self.nets.items() if mod == module_name}

    def pin_directions_for(self, module: Module) -> dict[str, dict[str, PinDirection]]:
        """Resolved ``in=`` / ``out=`` directions for ``module``'s children.

        Keyed by instance name, then formal pin name — the shape
        :func:`rtl_buddy_view.connectivity.scope_connectivity`
        consumes. Module-level hints (keyed by the child's module
        name — the sidecar's natural spelling for vendor IP) apply to
        every placement; instance-level pin hints, when any are
        present, replace them wholesale — one author statement per
        placement, rather than a per-pin interleave nobody can
        predict from the source.

        Instances with no hinted pins are absent from the result, so
        an empty dict means "nothing to say" everywhere.
        """
        resolved: dict[str, dict[str, PinDirection]] = {}
        for inst in module.instances:
            module_h = self.modules.get(inst.module_name)
            inst_h = self.instances.get((module.name, inst.name))
            source: ModuleHints | InstanceHints | None = module_h
            if inst_h is not None and (
                inst_h.in_pins is not None or inst_h.out_pins is not None
            ):
                source = inst_h
            if source is None:
                continue
            directions: dict[str, PinDirection] = {}
            for pin in source.in_pins or ():
                directions[pin] = "input"
            for pin in source.out_pins or ():
                directions[pin] = "output"
            if directions:
                resolved[inst.name] = directions
        return resolved


@dataclass(frozen=True)
class _Decl:
    """A pragma-attachable declaration recovered from the module table."""

    line: int
    kind: Literal["module", "instance", "net"]
    module: str  # defining module, or the enclosing module otherwise
    instance: str | None = None
    net: str | None = None
    #: A ``net`` declaration that is one of the module's ports — the
    #: only net a ``hide`` can bind to (rtl-buddy-sch#196).
    is_port: bool = False


_KIND_ORDER = {"instance": 0, "module": 1, "net": 2}


def _declaration_index(table: ModuleTable) -> dict[str, tuple[_Decl, ...]]:
    """Per-file, line-sorted declarations a pragma can bind to.

    Ties sort instances before modules so a same-line collision (a
    one-line module wrapping an instantiation) resolves to the more
    specific target deterministically. Net declarations and ports —
    the phase-2 targets — sort last on a line, which is invisible in
    practice: box association and net association each filter to
    their own kind before looking.

    An instance inside a generate loop is one declaration however many
    iterations it unrolls to: the table carries ``g[0].u_x`` and
    ``g[1].u_x`` at the same source line, and both collapse to the
    template ``g[*].u_x`` (:func:`generate_template`), so a pragma on
    the instantiation speaks for every iteration — in every placement,
    once :func:`bind_hint_keys` expands it (rtl-buddy-sch#217).
    """
    per_file: dict[str, list[_Decl]] = {}
    seen: set[tuple[str, _Decl]] = set()

    def _add(file: str, decl: _Decl) -> None:
        if (file, decl) in seen:
            return
        seen.add((file, decl))
        per_file.setdefault(file, []).append(decl)

    for module in table.modules_by_name.values():
        loc = module.location
        if loc is not None and loc.start_line is not None:
            _add(
                loc.file, _Decl(line=loc.start_line, kind="module", module=module.name)
            )
        for inst in module.instances:
            iloc = inst.location
            if iloc is None or iloc.start_line is None:
                continue
            template = generate_template(inst.name)
            _add(
                iloc.file,
                _Decl(
                    line=iloc.start_line,
                    kind="instance",
                    module=module.name,
                    instance=template,
                ),
            )
            # An interface instance is also a *net* target: it is the
            # wire its members ride on, and connectivity keys those
            # members under its name (rtl-buddy-sch#193), so a net
            # pragma on ``cfg_if cfg ();`` speaks for every ``cfg.*``.
            if inst.module_name in table.interfaces_by_name:
                _add(
                    iloc.file,
                    _Decl(
                        line=iloc.start_line,
                        kind="net",
                        module=module.name,
                        net=template,
                    ),
                )
        # Nets: body declarations and ports both — a clock is very
        # often a port of the scope being drawn, and both carry the
        # SourceLocation association needs.
        for net in module.net_decls:
            nloc = net.location
            if nloc is None or nloc.start_line is None:
                continue
            _add(
                nloc.file,
                _Decl(
                    line=nloc.start_line,
                    kind="net",
                    module=module.name,
                    net=net.name,
                ),
            )
        for port in module.ports:
            ploc = port.location
            if ploc is None or ploc.start_line is None:
                continue
            _add(
                ploc.file,
                _Decl(
                    line=ploc.start_line,
                    kind="net",
                    module=module.name,
                    net=port.name,
                    is_port=True,
                ),
            )
    return {
        file: tuple(
            sorted(
                decls,
                key=lambda d: (
                    d.line,
                    _KIND_ORDER[d.kind],
                    d.module,
                    d.instance or "",
                    d.net or "",
                ),
            )
        )
        for file, decls in sorted(per_file.items())
    }


def _associate_box(pragma: Pragma, decls: Sequence[_Decl]) -> _Decl | None:
    """Bind ``pragma`` to a module / instance declaration (phase-1 rules).

    - Trailing pragma (code to its left): the declaration *starting*
      on that line. A trailing pragma on a non-declaration line binds
      to nothing — falling through to the next declaration would make
      a stray comment silently reshape a diagram several lines away.
    - Standalone pragma: the nearest declaration starting below it in
      the same file.

    Filters to box declarations first, so the phase-2 net targets
    can never re-bind a phase-1 pragma: ``// rbsch: collapse`` above
    a net declaration still reaches the instance below it.
    """
    box = [d for d in decls if d.kind != "net"]
    if not pragma.own_line:
        for decl in box:
            if decl.line == pragma.line:
                return decl
        return None
    for decl in box:
        if decl.line > pragma.line:
            return decl
    return None


def _associate_nets(
    pragma: Pragma, decls: Sequence[_Decl], *, run: bool = False
) -> tuple[_Decl, ...]:
    """Bind a net pragma to the net / port declarations it covers.

    - Trailing: every net declarator *starting on that line* —
      ``logic a, b;  // rbsch: clock`` flags both.
    - Standalone: the next net or port declaration below it, again
      covering every declarator on that declaration's line.

    By default one *line* of declarators, never a run: classification
    is per-net intent, and spilling onto the following declarations
    would let one comment silently reclassify half a module.

    ``run=True`` — the ``bundle=`` association rule — extends a
    *standalone* pragma over the contiguous run of net declarations
    below it (consecutive source lines, multiple declarators per line
    included). A bundle is by definition a statement about a group,
    and the epic's canonical spelling is one comment above the
    member declarations; a blank line (or any non-declaration line)
    ends the run, so the group stays exactly the visually grouped
    block of code.
    """
    net_decls = [d for d in decls if d.kind == "net"]
    if not pragma.own_line:
        return tuple(d for d in net_decls if d.line == pragma.line)
    below = [d for d in net_decls if d.line > pragma.line]
    if not below:
        return ()
    covered = [below[0]]
    for decl in below[1:]:
        last_line = covered[-1].line
        if decl.line == last_line or (run and decl.line == last_line + 1):
            covered.append(decl)
        else:
            break
    return tuple(covered)


def _associate_port_hide(pragma: Pragma, decls: Sequence[_Decl]) -> tuple[_Decl, ...]:
    """The port declarations a ``hide`` binds to, or ``()`` for the box rule.

    ``hide`` is a box word, so it keeps every binding it had: an
    instance on the pragma's line (trailing), or the next box
    declaration below it (standalone), even past a plain ``logic``
    declaration. It binds to **ports** instead only where a port
    declaration is the nearer target — on the pragma's own line with
    no box declaration there, or (standalone) on the line below the
    pragma before the next box declaration starts. A standalone
    ``hide`` above a port used to fall through to the first instance
    in the module body, which no author meant.
    """
    box_lines = [d.line for d in decls if d.kind != "net"]
    nets = [d for d in decls if d.kind == "net"]
    if not pragma.own_line:
        if pragma.line in box_lines:
            return ()
        return tuple(d for d in nets if d.line == pragma.line and d.is_port)
    below = [d for d in nets if d.line > pragma.line]
    if not below or not below[0].is_port:
        return ()
    next_box = min((line for line in box_lines if line > pragma.line), default=None)
    if next_box is not None and next_box <= below[0].line:
        return ()
    return tuple(d for d in below if d.line == below[0].line and d.is_port)


def _describe(decl: _Decl) -> str:
    if decl.kind == "instance":
        return f"instance {decl.instance!r} in module {decl.module!r}"
    if decl.kind == "net":
        return f"net {decl.net!r} in module {decl.module!r}"
    return f"module {decl.module!r}"


def resolve_hints(pragmas: Sequence[Pragma], table: ModuleTable) -> HintMap:
    """Bind scanned pragmas to modules / instances / nets in ``table``.

    A payload may mix the box vocabulary (``leaf`` / ``collapse`` /
    ``hide`` / ``label``) with the net vocabulary (``clock`` /
    ``reset`` / ``data`` / ``main`` / ``side``). Each family
    associates independently, against declarations of its own kind —
    which is what keeps phase 1 stable: teaching the language about
    nets cannot re-bind a pragma that never mentions one.

    Wrong-target-kind use (``leaf`` on an instance, ``collapse`` on a
    module) is a warning, not an error: the intent is legible, the
    placement isn't, and the message says where it belongs.
    """
    warnings: list[str] = []
    modules: dict[str, ModuleHints] = {}
    instances: dict[tuple[str, str], InstanceHints] = {}
    nets: dict[tuple[str, str], NetHints] = {}
    index = _declaration_index(table)

    for pragma in pragmas:
        where = f"{pragma.file}:{pragma.line}"
        warnings.extend(f"{where}: {w}" for w in pragma.payload.warnings)
        if pragma.payload.is_empty:
            continue
        decls = index.get(pragma.file, ())
        box_flags = tuple(f for f in pragma.flags if f in KNOWN_FLAGS)
        net_flags = tuple(f for f in pragma.flags if f in KNOWN_NET_FLAGS)
        box_options = tuple(
            (k, v) for k, v in pragma.payload.options if k in KNOWN_KEYS
        )
        net_options = tuple(
            (k, v) for k, v in pragma.payload.options if k in KNOWN_NET_KEYS
        )
        if "hide" in box_flags:
            ports = _associate_port_hide(pragma, decls)
            if ports:
                # ``hide`` on a port declaration hides that port's
                # boundary flag (rtl-buddy-sch#196) — the port is the
                # nearer declaration, so the word is about it.
                box_flags = tuple(f for f in box_flags if f != "hide")
                for decl in ports:
                    assert decl.net is not None
                    port_key = (decl.module, decl.net)
                    nets[port_key] = replace(nets.get(port_key, NetHints()), hide=True)
        if box_flags or box_options:
            _resolve_box(
                pragma, where, box_flags, decls, modules, instances, warnings, table
            )
        if net_flags or net_options:
            _resolve_nets(pragma, where, net_flags, net_options, decls, nets, warnings)

    return HintMap(
        modules={k: v for k, v in sorted(modules.items()) if not v.is_empty},
        instances={k: v for k, v in sorted(instances.items()) if not v.is_empty},
        nets={k: v for k, v in sorted(nets.items()) if not v.is_empty},
        warnings=tuple(warnings),
    )


def _parse_pin_lists(
    pragma: Pragma, where: str, warnings: list[str]
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    """Parse ``in=`` / ``out=`` comma-separated pin lists.

    A pin spelled on both sides is a contradiction, not a direction;
    it warns and drops from both, leaving that pin exactly as
    unresolved as it was.
    """
    lists: dict[str, tuple[str, ...] | None] = {}
    for key in PIN_DIRECTION_KEYS:
        raw = pragma.option(key)
        if raw is None:
            lists[key] = None
            continue
        pins = tuple(pin.strip() for pin in raw.split(",") if pin.strip())
        if not pins:
            warnings.append(
                f"{where}: {key} needs a comma-separated pin list "
                f'(write {key}="pin_a,pin_b"); ignored'
            )
            lists[key] = None
            continue
        lists[key] = tuple(sorted(set(pins)))
    in_pins, out_pins = lists["in"], lists["out"]
    if in_pins is not None and out_pins is not None:
        both = sorted(set(in_pins) & set(out_pins))
        if both:
            warnings.append(
                f"{where}: {', '.join(both)} named in both in= and out=; "
                f"dropped from both"
            )
            in_pins = tuple(x for x in in_pins if x not in both) or None
            out_pins = tuple(x for x in out_pins if x not in both) or None
    return in_pins, out_pins


def _instance_module(table: ModuleTable | None, decl: _Decl) -> Module | None:
    """The module an instance declaration places, when the table has it."""
    if table is None:
        return None
    parent = table.modules_by_name.get(decl.module)
    if parent is None:
        return None
    for inst in parent.instances:
        if generate_template(inst.name) == decl.instance:
            return table.modules_by_name.get(inst.module_name)
    return None


def parse_params_value(text: str) -> ParamsHint | None:
    """``params=`` value → hint: ``all``, ``none`` or a name list.

    ``None`` when the value names nothing (``params=`` or ``params=,``).
    """
    value = text.strip()
    if value in ("all", "none"):
        return cast(ParamsHint, value)
    names = tuple(sorted({n.strip() for n in value.split(",") if n.strip()}))
    return names or None


def _parse_params(
    pragma: Pragma,
    where: str,
    warnings: list[str],
    module: Module | None,
    target: str,
) -> ParamsHint | None:
    """Parse one pragma's ``params=``, warning on names the module lacks.

    An unknown name is kept — the module might come from a sibling
    filelist next run — but warned about, since a typo would otherwise
    silently hide the parameter the author meant to keep.
    """
    raw = pragma.option("params")
    if raw is None:
        return None
    hint = parse_params_value(raw)
    if hint is None:
        warnings.append(
            f'{where}: params needs all, none or a parameter list (write params="W,DEPTH"); ignored'
        )
        return None
    if isinstance(hint, tuple) and module is not None:
        known = {p.name for p in module.parameters}
        unknown = [name for name in hint if name not in known]
        if unknown:
            warnings.append(
                f"{where}: params names {', '.join(unknown)}, not a parameter "
                f"of module {module.name!r} ({target}); known: "
                f"{', '.join(sorted(known)) or '(none)'}"
            )
    return hint


def _resolve_box(
    pragma: Pragma,
    where: str,
    box_flags: tuple[str, ...],
    decls: Sequence[_Decl],
    modules: dict[str, ModuleHints],
    instances: dict[tuple[str, str], InstanceHints],
    warnings: list[str],
    table: ModuleTable | None = None,
) -> None:
    """Apply one pragma's box vocabulary (phase-1 body, unchanged rules)."""
    decl = _associate_box(pragma, decls)
    if decl is None:
        warnings.append(
            f"{where}: {PRAGMA_PREFIX} pragma matched no module or "
            f"instance declaration ("
            + (
                "a trailing pragma binds to a declaration starting on the same line"
                if not pragma.own_line
                else "a standalone pragma binds to the next declaration below it"
            )
            + ")"
        )
        return

    label = pragma.option("label")
    if decl.kind == "instance":
        assert decl.instance is not None
        key = (decl.module, decl.instance)
        current = instances.get(key, InstanceHints())
        for flag in box_flags:
            if flag in MODULE_ONLY_FLAGS:
                warnings.append(
                    f"{where}: {flag!r} applies to a module definition, "
                    f"not {_describe(decl)}; move it above the module "
                    f"declaration (or use 'collapse' here)"
                )
            elif flag == "collapse":
                current = replace(current, collapse=True)
            elif flag == "hide":
                current = replace(current, hide=True)
        if label is not None:
            current = replace(current, label=label)
        rank_text = pragma.option("rank")
        if rank_text is not None:
            try:
                current = replace(current, rank=int(rank_text, base=10))
            except ValueError:
                warnings.append(
                    f"{where}: rank must be an integer (got {rank_text!r}); ignored"
                )
        group = pragma.option("group")
        if group is not None:
            if group:
                current = replace(current, group=group)
            else:
                warnings.append(f"{where}: group needs a name (write group=…); ignored")
        in_pins, out_pins = _parse_pin_lists(pragma, where, warnings)
        if in_pins is not None:
            current = replace(current, in_pins=in_pins)
        if out_pins is not None:
            current = replace(current, out_pins=out_pins)
        params = _parse_params(
            pragma, where, warnings, _instance_module(table, decl), _describe(decl)
        )
        if params is not None:
            current = replace(current, params=params)
        instances[key] = current
    else:
        current_m = modules.get(decl.module, ModuleHints())
        for flag in box_flags:
            if flag in INSTANCE_ONLY_FLAGS:
                warnings.append(
                    f"{where}: {flag!r} applies to an instance, not "
                    f"{_describe(decl)}; move it onto the instantiation "
                    f"(or use 'leaf' here)"
                )
            elif flag == "leaf":
                current_m = replace(current_m, leaf=True)
        for key_name in INSTANCE_ONLY_KEYS:
            if pragma.option(key_name) is not None:
                warnings.append(
                    f"{where}: {key_name!r} applies to an instance, not "
                    f"{_describe(decl)}; move it onto the instantiation"
                )
        if label is not None:
            current_m = replace(current_m, label=label)
        in_pins, out_pins = _parse_pin_lists(pragma, where, warnings)
        if in_pins is not None:
            current_m = replace(current_m, in_pins=in_pins)
        if out_pins is not None:
            current_m = replace(current_m, out_pins=out_pins)
        params = _parse_params(
            pragma,
            where,
            warnings,
            table.modules_by_name.get(decl.module) if table is not None else None,
            _describe(decl),
        )
        if params is not None:
            current_m = replace(current_m, params=params)
        modules[decl.module] = current_m


def _resolve_nets(
    pragma: Pragma,
    where: str,
    net_flags: tuple[str, ...],
    net_options: tuple[tuple[str, str], ...],
    decls: Sequence[_Decl],
    nets: dict[tuple[str, str], NetHints],
    warnings: list[str],
) -> None:
    """Apply one pragma's net vocabulary to its covered declarators."""
    bundle: str | None = None
    color: str | None = None
    for key, value in net_options:
        if key == "bundle":
            if value:
                bundle = value
            else:
                warnings.append(
                    f"{where}: bundle needs a name (write bundle=…); ignored"
                )
        elif key == "color":
            color = parse_color(value)
            if color is None:
                warnings.append(
                    f"{where}: color {value!r} is not a colour; use "
                    f"{color_spelling_help()}; ignored"
                )
    # A bundle is a statement about a group, so its standalone form
    # covers the contiguous declaration run — and the other net words
    # on the same pragma ride along: the author grouped those
    # declarations deliberately.
    targets = _associate_nets(pragma, decls, run=bundle is not None)
    if not targets:
        spelled = ", ".join(net_flags + tuple(f"{k}=…" for k, _ in net_options))
        warnings.append(
            f"{where}: net pragma ({spelled}) matched no net "
            f"or port declaration ("
            + (
                "a trailing net pragma binds to the declarators starting "
                "on the same line"
                if not pragma.own_line
                else "a standalone net pragma binds to the next net or "
                "port declaration below it"
            )
            + ")"
        )
        return

    classifications = tuple(f for f in net_flags if f in CLASSIFICATION_FLAGS)
    emphases = tuple(f for f in net_flags if f in EMPHASIS_FLAGS)
    classification: Classification | None = None
    emphasis: Emphasis | None = None
    if len(set(classifications)) > 1:
        warnings.append(
            f"{where}: {', '.join(sorted(set(classifications)))} are "
            f"mutually exclusive; classification dropped"
        )
    elif classifications:
        classification = cast(Classification, classifications[0])
    if len(set(emphases)) > 1:
        warnings.append(
            f"{where}: {', '.join(sorted(set(emphases)))} are mutually "
            f"exclusive; emphasis dropped"
        )
    elif emphases:
        emphasis = cast(Emphasis, emphases[0])
    if color is not None and classification in ("data", "reset"):
        # Only clocks are coloured; a colour on a net the same pragma
        # calls data or a reset would silently do nothing.
        warnings.append(
            f"{where}: color applies to clocks, not a net classified "
            f"{classification!r}; color dropped"
        )
        color = None
    if classification is None and emphasis is None and bundle is None and color is None:
        return

    for decl in targets:
        assert decl.net is not None
        net_key = (decl.module, decl.net)
        current = nets.get(net_key, NetHints())
        if classification is not None:
            current = replace(current, classification=classification)
        if emphasis is not None:
            current = replace(current, emphasis=emphasis)
        if bundle is not None:
            current = replace(current, bundle=bundle)
        if color is not None:
            current = replace(current, color=color)
        nets[net_key] = current


#: Any one of the three hint records (they share :func:`_override`).
_H = TypeVar("_H", ModuleHints, InstanceHints, NetHints)


def _override(base: _H | None, over: _H) -> _H:
    """``over`` laid field-wise onto ``base``: a ``None`` field says nothing.

    The one merge rule every hint layer shares — sidecar over pragma
    (:func:`merge_hint_maps`) and exact key over wildcard key
    (:func:`bind_hint_keys`). All three hint records are flat frozen
    dataclasses whose every field is tri-state, so the rule is the same
    for each.
    """
    if base is None:
        return over
    changes = {
        name: value
        for name in over.__dataclass_fields__
        if (value := getattr(over, name)) is not None
    }
    return replace(base, **changes)


def merge_hint_maps(base: HintMap, override: HintMap) -> HintMap:
    """Field-wise merge with ``override`` winning.

    Used for "sidecar over in-source pragma". The merge is per field,
    not per target: a sidecar that only renames a block keeps the
    in-source ``collapse`` on the same instance. ``None`` means
    "unspecified", so a sidecar can also *revoke* an in-source flag
    by spelling ``false``.
    """
    modules = dict(base.modules)
    for name, hints in override.modules.items():
        modules[name] = _override(modules.get(name), hints)
    instances = dict(base.instances)
    for key, inst_hints in override.instances.items():
        instances[key] = _override(instances.get(key), inst_hints)
    nets = dict(base.nets)
    for net_key, net_hints in override.nets.items():
        nets[net_key] = _override(nets.get(net_key), net_hints)
    return HintMap(
        modules=dict(sorted(modules.items())),
        instances=dict(sorted(instances.items())),
        nets=dict(sorted(nets.items())),
        warnings=base.warnings + override.warnings,
    )


# --- keys (rtl-buddy-sch#217) ------------------------------------------------
#
# An instance or net inside a generate block is named the way
# :mod:`rtl_buddy_view.generate` unrolls it: ``g[0].u_x`` (loop
# iteration), ``g.u_x`` (conditional arm), ``g[*].u_x`` (a loop whose
# bounds don't fold), nested as deep as the source nests. A key is
# therefore ``<module>.<path>``, split on the *first* dot, where the path
# is the name exactly as every output prints it. ``*`` in the path is a
# wildcard for any run of characters within one dot-separated segment,
# brackets included — so ``g[*]`` is every iteration of ``g`` (and the
# symbolic ``g[*]`` itself), and ``u_*`` every name starting ``u_``.

_INDEX_RE = re.compile(r"\[[^\[\]]*\]")


def _top_level_cuts(path: str, *, brackets: bool) -> list[int]:
    """Offsets of the ``.`` (and with ``brackets``, ``[``) outside any ``[…]``."""
    cuts: list[int] = []
    depth = 0
    for i, ch in enumerate(path):
        if ch == "[":
            if depth == 0 and brackets and i:
                cuts.append(i)
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "." and depth == 0:
            cuts.append(i)
    return cuts


def generate_template(name: str) -> str:
    """``g[0].h[1].u_x`` → ``g[*].h[*].u_x``: the name every iteration shares.

    Only the generate scope — everything before the last top-level dot
    — is rewritten; the leaf keeps its own brackets. A name outside any
    generate block (or in a conditional arm only, ``g.u_x``) comes back
    unchanged. This is how an in-source pragma on an instantiation
    inside a loop body reaches every iteration rather than the first.
    """
    cuts = _top_level_cuts(name, brackets=False)
    if not cuts:
        return name
    cut = cuts[-1]
    return _INDEX_RE.sub(f"[{WILDCARD}]", name[:cut]) + name[cut:]


def _path_error(path: str) -> str | None:
    """Why ``path`` is not a well-formed instance / net path, or ``None``.

    The grammar is ``segment ('.' segment)*`` with ``segment`` a name
    followed by zero or more ``[index]`` groups. Names and indices are
    otherwise free text: an escaped identifier, a parameter-named
    index (``dv[SR_A]``) and ``*`` are all legal.
    """
    if not path:
        return "nothing after the module name"
    expect_name = True  # at a segment start: a name must come next
    after_index = False  # just closed a ']': only '[', '.' or the end
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == "[":
            if expect_name:
                return f"'[' at offset {i} must follow a name"
            close = path.find("]", i + 1)
            nested = path.find("[", i + 1)
            if close == -1:
                return f"unbalanced '[' at offset {i}"
            if nested != -1 and nested < close:
                return f"nested '[' at offset {nested}"
            if close == i + 1:
                return f"empty index '[]' at offset {i}"
            after_index = True
            i = close + 1
            continue
        if ch == "]":
            return f"unbalanced ']' at offset {i}"
        if ch == ".":
            if expect_name:
                return f"empty path segment (stray '.' at offset {i})"
            expect_name = True
            after_index = False
        elif after_index:
            return (
                f"unexpected {ch!r} after ']' at offset {i} "
                f"(expected '[', '.' or the end)"
            )
        else:
            expect_name = False
        i += 1
    if expect_name:
        return "empty path segment (trailing '.')"
    return None


def parse_hint_key(text: str) -> tuple[str, str]:
    """Split a sidecar ``instances`` / ``nets`` key into ``(module, path)``.

    The split is on the *first* dot: a module name never contains one,
    while a generate-scoped path does (``top.g[0].u_x``). Raises
    :class:`ValueError` naming what is wrong; the sidecar loader turns
    that into a :class:`HintsError` with the key's location.
    """
    module, sep, path = text.partition(".")
    if not sep:
        raise ValueError("no '.' between the module and the name")
    if not module:
        raise ValueError("empty module name")
    if any(ch in module for ch in "[]" + WILDCARD):
        raise ValueError(
            f"module name {module!r} may not contain '[', ']' or {WILDCARD!r}"
        )
    reason = _path_error(path)
    if reason is not None:
        raise ValueError(f"{reason} in {path!r}")
    return module, path


def key_pattern(path: str) -> re.Pattern[str] | None:
    """The full-match regex for a wildcard key path, or ``None`` when exact.

    ``*`` matches any run of characters — possibly none — that holds no
    ``.``: it never crosses into another path segment. Everything else
    matches itself.
    """
    if WILDCARD not in path:
        return None
    return re.compile("[^.]*".join(re.escape(part) for part in path.split(WILDCARD)))


def _literal_length(path: str) -> int:
    """How specific a wildcard key is: its count of non-wildcard characters."""
    return len(path) - path.count(WILDCARD)


@dataclass(frozen=True)
class DesignNames:
    """The instance and net names each module has, for key binding.

    Keyed by module name. Instance names are every name a placement of
    the module elaborates to — a parameter override that changes a loop
    bound adds names (``g[3].u_x``) the table's default elaboration
    lacks. Net names are the module's declared nets, its ports, its
    interface instances and its generate-local nets, qualified with
    their scope (``g[0].w``).
    """

    instances: Mapping[str, frozenset[str]] = field(default_factory=dict)
    nets: Mapping[str, frozenset[str]] = field(default_factory=dict)


def design_names(table: ModuleTable, root: HierNode | None = None) -> DesignNames:
    """Collect :class:`DesignNames` from ``table`` and, if given, ``root``.

    The table contributes every module's default elaboration, so a key
    on a module outside the rendered subtree still binds; ``root``
    contributes each placement's own elaboration, which is what the
    renderers draw.
    """
    instances: dict[str, set[str]] = {}
    nets: dict[str, set[str]] = {}
    seen: set[tuple[int, tuple[tuple[str, str | None], ...]]] = set()

    def add(module: Module, env: Mapping[str, str | None]) -> None:
        memo = (id(module), tuple(sorted(env.items(), key=lambda kv: kv[0])))
        if memo in seen:
            return
        seen.add(memo)
        inst_names = instances.setdefault(module.name, set())
        net_names = nets.setdefault(module.name, set())
        for inst in module.instances:
            inst_names.add(inst.name)
            if inst.module_name in table.interfaces_by_name:
                net_names.add(inst.name)
        net_names.update(decl.name for decl in module.net_decls)
        net_names.update(port.name for port in module.ports)
        if module.generate_body:
            net_names.update(scoped_net_names(module.generate_body, env))

    for module in table.modules_by_name.values():
        add(module, module_env(module))

    def walk(node: HierNode, env: Mapping[str, str | None]) -> None:
        if node.module is None:
            return
        add(node.module, env)
        for child in node.children:
            source = table.modules_by_name.get(child.module_name)
            if child.instance is None or source is None:
                continue
            walk(
                child,
                module_env(source, child.instance.param_overrides, outer_env=env),
            )

    if root is not None:
        top = table.modules_by_name.get(root.module_name)
        if top is not None:
            walk(root, module_env(top))

    return DesignNames(
        instances={k: frozenset(v) for k, v in sorted(instances.items())},
        nets={k: frozenset(v) for k, v in sorted(nets.items())},
    )


def _names_target(path: str, known: frozenset[str], *, members: bool) -> bool:
    """Whether an exact key path names something in ``known``.

    With ``members`` (net keys), a path that *extends* a known net at a
    ``.`` / ``[`` boundary also counts: ``cfg.a_en`` is a member of the
    interface ``cfg``, ``dv[SR_A]`` an element of the vector ``dv`` —
    the connectivity analyzer looks those keys up verbatim.
    """
    if path in known:
        return True
    if not members:
        return False
    return any(path[:cut] in known for cut in _top_level_cuts(path, brackets=True))


def _expand(path: str, known: frozenset[str], *, members: bool) -> list[str]:
    """The concrete names a wildcard key path binds to, sorted.

    Every known name the pattern matches in full; with ``members``
    (net keys), also a known net matched by the pattern's head with the
    wildcard-free rest of the key appended — ``g[*].sub[1]`` reaches
    element ``[1]`` of every iteration's ``sub``.
    """
    pattern = key_pattern(path)
    assert pattern is not None
    hits = {name for name in known if pattern.fullmatch(name)}
    if members:
        for cut in _top_level_cuts(path, brackets=True):
            head, tail = path[:cut], path[cut:]
            head_pattern = key_pattern(head)
            if head_pattern is None or WILDCARD in tail:
                continue
            hits.update(name + tail for name in known if head_pattern.fullmatch(name))
    return sorted(hits)


def _names_nothing(module: str, noun: str, known: Mapping[str, frozenset[str]]) -> str:
    names = known.get(module)
    if names is None:
        return f"module {module!r} is not in the design; the key names nothing"
    if not names:
        return f"module {module!r} has no {noun}s; the key names nothing"
    listed = sorted(names)
    shown = ", ".join(listed[:8]) + (", …" if len(listed) > 8 else "")
    return f"names no {noun} of module {module!r} (its {noun}s: {shown})"


def _bind_section(
    entries: Mapping[tuple[str, str], _H],
    known: Mapping[str, frozenset[str]],
    *,
    section: str,
    noun: str,
    members: bool,
    where: str,
    warnings: list[str],
) -> dict[tuple[str, str], _H]:
    bound: dict[tuple[str, str], _H] = {}
    wild = sorted(
        (
            (module, path, hint)
            for (module, path), hint in entries.items()
            if WILDCARD in path
        ),
        key=lambda e: (_literal_length(e[1]), e[1], e[0]),
    )
    # Least specific first, so a narrower wildcard's fields land on top.
    for module, path, hint in wild:
        matches = _expand(path, known.get(module, frozenset()), members=members)
        if not matches:
            warnings.append(
                f"{where}{section}.{module}.{path}: {_names_nothing(module, noun, known)}"
            )
            continue
        for name in matches:
            bound[(module, name)] = _override(bound.get((module, name)), hint)
    # An exact key beats every wildcard, field by field.
    for (module, path), hint in entries.items():
        if WILDCARD in path:
            continue
        if not _names_target(path, known.get(module, frozenset()), members=members):
            warnings.append(
                f"{where}{section}.{module}.{path}: {_names_nothing(module, noun, known)}"
            )
        bound[(module, path)] = _override(bound.get((module, path)), hint)
    return dict(sorted(bound.items()))


def bind_hint_keys(hints: HintMap, names: DesignNames) -> HintMap:
    """Expand wildcard keys and flag keys that name nothing (rtl-buddy-sch#217).

    Every downstream consumer looks hints up by a concrete
    ``(module, name)`` pair, so a wildcard key (``top.g[*].u_x``) is
    replaced by one entry per name it matches in ``names``. When
    several keys reach one target the fields merge: an exact key wins
    over any wildcard, and a wildcard with more literal characters over
    one with fewer (ties: the later key in sorted order).

    A key — wildcard or exact — that names no instance (no net) of its
    module is a warning, not an error: the sidecar may serve several
    tops or filelists. An exact key is kept regardless, so binding
    never changes what an old sidecar does. Module keys are not
    checked; the sidecar's ``in``/``out`` pin lists exist precisely for
    modules the design has no source for.

    Bind the pragma map and the sidecar map *separately*, before
    :func:`merge_hint_maps`, so a sidecar wildcard still beats an
    in-source pragma on the same instance.
    """
    if not hints.instances and not hints.nets:
        return hints
    warnings = list(hints.warnings)
    where = f"{hints.source}: " if hints.source else ""
    instances = _bind_section(
        hints.instances,
        names.instances,
        section="instances",
        noun="instance",
        members=False,
        where=where,
        warnings=warnings,
    )
    nets = _bind_section(
        hints.nets,
        names.nets,
        section="nets",
        noun="net",
        members=True,
        where=where,
        warnings=warnings,
    )
    return HintMap(
        modules=hints.modules,
        instances=instances,
        nets=nets,
        warnings=tuple(warnings),
        source=hints.source,
    )


# --- sidecar -----------------------------------------------------------------


def _major(version: str) -> int:
    head = version.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        raise HintsError(f"schema_version {version!r} is not a dotted number") from None


def _require_bool(value: object, *, where: str, key: str) -> bool:
    if not isinstance(value, bool):
        raise HintsError(
            f"{where}: {key!r} must be a boolean, got {type(value).__name__}"
        )
    return value


def _require_str(value: object, *, where: str, key: str) -> str:
    if not isinstance(value, str):
        raise HintsError(
            f"{where}: {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def _require_pin_list(value: object, *, where: str, key: str) -> tuple[str, ...]:
    """A JSON array of pin-name strings. ``[]`` is legal — the
    explicit revoke of an in-source list."""
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise HintsError(
            f"{where}: {key!r} must be an array of pin-name strings, "
            f"got {type(value).__name__}"
        )
    return tuple(sorted({x.strip() for x in value if x.strip()}))


def _require_int(value: object, *, where: str, key: str) -> int:
    # ``bool`` is an ``int`` subclass; a JSON ``true`` reaching a rank
    # is a mistake, not a column.
    if not isinstance(value, int) or isinstance(value, bool):
        raise HintsError(
            f"{where}: {key!r} must be an integer, got {type(value).__name__}"
        )
    return value


def _require_params(value: object, *, where: str) -> ParamsHint:
    """``"all"`` / ``"none"`` / an array of parameter names (``[]`` = none)."""
    if value in ("all", "none"):
        return cast(ParamsHint, value)
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        names = tuple(sorted({x.strip() for x in value if x.strip()}))
        return names if names else "none"
    raise HintsError(
        f'{where}: \'params\' must be "all", "none" or an array of '
        f"parameter names, got {value!r}"
    )


def parse_hint_sidecar(payload: object, *, source: str) -> HintMap:
    """Validate a decoded sidecar document into a :class:`HintMap`.

    Split from :func:`load_hint_sidecar` so the schema rules are
    testable without touching a filesystem. Unknown *entry* keys warn
    (same vocabulary-drift tolerance as the pragma parser); unknown
    top-level keys are ignored (forward compatibility, matching the
    clock-domain-map consumer rules); wrong *types* raise, because a
    sidecar is an artefact the user deliberately pointed at.

    ``instances`` / ``nets`` keys go through :func:`parse_hint_key`, so
    a malformed one raises here too. Wildcard keys are kept as written;
    :func:`bind_hint_keys` expands them once the design is known.
    """
    if not isinstance(payload, dict):
        raise HintsError(f"{source}: top-level must be a JSON object")
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise HintsError(f"{source}: schema_version missing or not a string")
    if _major(version) != SUPPORTED_SCHEMA_MAJOR:
        raise HintsError(
            f"{source}: schema_version {version!r} is not supported "
            f"(consumer expects {SUPPORTED_SCHEMA_MAJOR}.x)"
        )

    warnings: list[str] = []
    modules: dict[str, ModuleHints] = {}
    raw_modules = payload.get("modules", {})
    if not isinstance(raw_modules, dict):
        raise HintsError(f"{source}: 'modules' must be a JSON object")
    for name, entry in sorted(raw_modules.items()):
        where = f"{source}: modules.{name}"
        if not isinstance(entry, dict):
            raise HintsError(f"{where}: entry must be a JSON object")
        hints = ModuleHints()
        for key, value in sorted(entry.items()):
            if key == "leaf":
                hints = replace(hints, leaf=_require_bool(value, where=where, key=key))
            elif key == "label":
                hints = replace(hints, label=_require_str(value, where=where, key=key))
            elif key == "in":
                hints = replace(
                    hints, in_pins=_require_pin_list(value, where=where, key=key)
                )
            elif key == "out":
                hints = replace(
                    hints, out_pins=_require_pin_list(value, where=where, key=key)
                )
            elif key == "params":
                hints = replace(hints, params=_require_params(value, where=where))
            else:
                warnings.append(f"{where}: {_unknown_word_warning(key)}")
        if not hints.is_empty:
            modules[name] = hints

    instances: dict[tuple[str, str], InstanceHints] = {}
    raw_instances = payload.get("instances", {})
    if not isinstance(raw_instances, dict):
        raise HintsError(f"{source}: 'instances' must be a JSON object")
    for key_text, entry in sorted(raw_instances.items()):
        where = f"{source}: instances.{key_text}"
        try:
            inst_key = parse_hint_key(key_text)
        except ValueError as e:
            raise HintsError(
                f"{where}: key must be '<parent_module>.<instance_name>' "
                f"(generate-scoped: '<parent_module>.g[0].<instance_name>'): {e}"
            ) from None
        if not isinstance(entry, dict):
            raise HintsError(f"{where}: entry must be a JSON object")
        inst_hints = InstanceHints()
        for name, value in sorted(entry.items()):
            if name == "collapse":
                inst_hints = replace(
                    inst_hints, collapse=_require_bool(value, where=where, key=name)
                )
            elif name == "hide":
                inst_hints = replace(
                    inst_hints, hide=_require_bool(value, where=where, key=name)
                )
            elif name == "label":
                inst_hints = replace(
                    inst_hints, label=_require_str(value, where=where, key=name)
                )
            elif name == "rank":
                inst_hints = replace(
                    inst_hints, rank=_require_int(value, where=where, key=name)
                )
            elif name == "group":
                # ``""`` is legal here and only here: the sidecar's
                # explicit revoke of an in-source group membership.
                inst_hints = replace(
                    inst_hints, group=_require_str(value, where=where, key=name)
                )
            elif name == "in":
                inst_hints = replace(
                    inst_hints, in_pins=_require_pin_list(value, where=where, key=name)
                )
            elif name == "out":
                inst_hints = replace(
                    inst_hints, out_pins=_require_pin_list(value, where=where, key=name)
                )
            elif name == "params":
                inst_hints = replace(
                    inst_hints, params=_require_params(value, where=where)
                )
            else:
                warnings.append(f"{where}: {_unknown_word_warning(name)}")
        if not inst_hints.is_empty:
            instances[inst_key] = inst_hints

    nets: dict[tuple[str, str], NetHints] = {}
    raw_nets = payload.get("nets", {})
    if not isinstance(raw_nets, dict):
        raise HintsError(f"{source}: 'nets' must be a JSON object")
    for key_text, entry in sorted(raw_nets.items()):
        where = f"{source}: nets.{key_text}"
        try:
            net_key = parse_hint_key(key_text)
        except ValueError as e:
            raise HintsError(
                f"{where}: key must be '<module>.<net_name>' "
                f"(generate-scoped: '<module>.g[0].<net_name>'): {e}"
            ) from None
        if not isinstance(entry, dict):
            raise HintsError(f"{where}: entry must be a JSON object")
        net_hints = NetHints()
        for name, value in sorted(entry.items()):
            if name == "classification":
                text = _require_str(value, where=where, key=name)
                if text not in CLASSIFICATION_FLAGS:
                    # A wrong *value* raises like a wrong type does: a
                    # sidecar is a deliberate artefact, and a typo'd
                    # "clokc" silently classifying nothing is worse
                    # than a loud failure.
                    raise HintsError(
                        f"{where}: classification must be one of "
                        f"{', '.join(sorted(CLASSIFICATION_FLAGS))}; got {text!r}"
                    )
                net_hints = replace(
                    net_hints, classification=cast(Classification, text)
                )
            elif name == "emphasis":
                text = _require_str(value, where=where, key=name)
                if text not in EMPHASIS_FLAGS:
                    raise HintsError(
                        f"{where}: emphasis must be one of "
                        f"{', '.join(sorted(EMPHASIS_FLAGS))}; got {text!r}"
                    )
                net_hints = replace(net_hints, emphasis=cast(Emphasis, text))
            elif name == "bundle":
                # ``""`` is legal here and only here: the sidecar's
                # explicit revoke of an in-source bundle membership.
                net_hints = replace(
                    net_hints, bundle=_require_str(value, where=where, key=name)
                )
            elif name == "hide":
                net_hints = replace(
                    net_hints, hide=_require_bool(value, where=where, key=name)
                )
            elif name == "color":
                text = _require_str(value, where=where, key=name)
                # ``""`` is the explicit revoke of an in-source colour.
                normalised = parse_color(text) if text else ""
                if normalised is None:
                    raise HintsError(
                        f"{where}: color {text!r} is not a colour; use "
                        f"{color_spelling_help()}"
                    )
                net_hints = replace(net_hints, color=normalised)
            else:
                warnings.append(f"{where}: {_unknown_word_warning(name)}")
        if not net_hints.is_empty:
            nets[net_key] = net_hints

    return HintMap(
        modules=modules,
        instances=instances,
        nets=nets,
        warnings=tuple(warnings),
        source=source,
    )


def load_hint_sidecar(path: Path) -> HintMap:
    """Read + validate a hint sidecar.

    The one I/O entry point in this module, shaped exactly like
    :func:`rtl_buddy_view.annotations.load_domain_map`: read errors
    and JSON syntax errors are re-raised as :class:`HintsError` so a
    single ``except`` covers every "the sidecar didn't load" case.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise HintsError(f"could not read {path}: {e}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise HintsError(f"{path}: invalid JSON ({e.msg})") from None
    return parse_hint_sidecar(payload, source=str(path))


# --- graph transform ---------------------------------------------------------


def apply_hints(
    root: HierNode,
    hints: HintMap,
    *,
    warnings: MutableSequence[str] | None = None,
) -> HierNode:
    """Rewrite the hierarchy according to ``hints``.

    ``hide`` drops a node and everything under it. ``collapse`` (on an
    instance) and ``leaf`` (on a module) drop a node's children so it
    renders as a leaf box. Both are *graph* edits rather than renderer
    behaviour, so dot, tree, mermaid and JSON agree on what exists —
    the phase-1 rule from the epic.

    :class:`HierNode` is frozen; nodes are rebuilt with
    :func:`dataclasses.replace` and untouched subtrees are returned
    by identity, so an empty map is free and output stays
    byte-identical to the no-hints case.

    The top node is never hidden or collapsed — a figure with nothing
    in it is never what the author meant. ``hide``/``collapse`` can't
    even name it (they key on the parent module, and the top has no
    parent), so the reachable case is a ``leaf`` module hint on the
    top module itself; that warns and is ignored. ``label`` on the top
    module still applies.

    ``warnings`` is an optional sink the CLI passes so refusals reach
    stderr; the function itself performs no I/O.
    """
    sink: MutableSequence[str] = [] if warnings is None else warnings
    if hints.is_empty:
        return root

    module_hints = hints.for_module(root.module_name)
    if module_hints is not None and module_hints.leaf:
        sink.append(
            f"'leaf' on top module {root.module_name!r} would empty the "
            f"diagram; ignored"
        )
    label = module_hints.label if module_hints is not None else None
    children = _apply_children(root, hints, sink)
    if label is None and children == root.children:
        return root
    return replace(root, children=children, display_label=label)


def _display_params(
    inst_hints: InstanceHints | None, module_hints: ModuleHints | None
) -> tuple[str, ...] | None:
    """:attr:`HierNode.display_params` from the node's ``params=`` hints.

    The instance's word beats the module's, like ``label``.
    """
    hint: ParamsHint | None = None
    if inst_hints is not None and inst_hints.params is not None:
        hint = inst_hints.params
    elif module_hints is not None:
        hint = module_hints.params
    if hint is None or hint == "all":
        return None
    if hint == "none":
        return ()
    return hint


def hide_all_params(root: HierNode) -> HierNode:
    """``--params none``: no node shows a parameter row.

    The CLI's figure-wide switch, applied after :func:`apply_hints`
    so it overrides every ``params=`` pragma. A graph edit for the same
    reason ``label`` is one: the renderers just read
    :attr:`HierNode.display_params`. Untouched subtrees are returned
    by identity.
    """
    children = tuple(hide_all_params(c) for c in root.children)
    params = root.display_params
    if root.instance is not None and root.instance.param_overrides:
        params = ()
    if params == root.display_params and children == root.children:
        return root
    return replace(root, children=children, display_params=params)


def _apply_children(
    node: HierNode, hints: HintMap, sink: MutableSequence[str]
) -> tuple[HierNode, ...]:
    kept: list[HierNode] = []
    for child in node.children:
        rewritten = _apply_node(child, hints, node.module_name, sink)
        if rewritten is not None:
            kept.append(rewritten)
    return tuple(kept)


def _apply_node(
    node: HierNode,
    hints: HintMap,
    parent_module: str,
    sink: MutableSequence[str],
) -> HierNode | None:
    inst_hints = (
        hints.for_instance(parent_module, node.instance.name)
        if node.instance is not None
        else None
    )
    if inst_hints is not None and inst_hints.hide:
        return None
    module_hints = hints.for_module(node.module_name)

    # Instance label wins over module label: the instance is the more
    # specific statement ("this copy is the write path"), the module
    # label is the default for every copy.
    label = None
    if inst_hints is not None and inst_hints.label is not None:
        label = inst_hints.label
    elif module_hints is not None:
        label = module_hints.label

    collapse = bool(inst_hints is not None and inst_hints.collapse) or bool(
        module_hints is not None and module_hints.leaf
    )
    children = () if collapse else _apply_children(node, hints, sink)
    params = _display_params(inst_hints, module_hints)
    if label is None and params is None and children == node.children:
        return node
    return replace(node, children=children, display_label=label, display_params=params)
