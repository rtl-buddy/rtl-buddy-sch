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

Unknown vocabulary never raises. A typo'd key is collected as a warning
naming the known set (the self-correcting-typo pattern the overlay
registry uses for unknown overlay names) so a bad pragma degrades to
"no hint" rather than "no diagram".
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from rtl_buddy_view.extractor import ModuleTable
from rtl_buddy_view.graph import HierNode

#: Comment prefix this tool answers to. The ``rb`` stem is the
#: rtl-buddy-wide in-source pragma convention; the suffix names the
#: consuming tool so a design can carry ``rbcdc`` / ``rbxeno``
#: pragmas side by side without either tool guessing.
PRAGMA_PREFIX = "rbsch"

#: Sidecar schema version this loader emits/accepts. Same
#: major-must-match / minor-may-drift rule as the clock-domain map.
SIDECAR_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_MAJOR = 1

#: Valueless pragma words.
KNOWN_FLAGS: tuple[str, ...] = ("collapse", "hide", "leaf")

#: ``key=value`` pragma words.
KNOWN_KEYS: tuple[str, ...] = ("label",)

#: Flags that only mean something on a module declaration.
MODULE_ONLY_FLAGS: tuple[str, ...] = ("leaf",)

#: Flags that only mean something on an instance.
INSTANCE_ONLY_FLAGS: tuple[str, ...] = ("collapse", "hide")


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
        f"{', '.join(KNOWN_FLAGS)}; known keys: "
        f"{', '.join(f'{k}=…' for k in KNOWN_KEYS)}"
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
            if token in KNOWN_FLAGS:
                flags.add(token)
            elif token in KNOWN_KEYS:
                warnings.append(
                    f'{PRAGMA_PREFIX} key {token!r} needs a value (write {token}="…")'
                )
            else:
                warnings.append(_unknown_word_warning(token))
            continue
        if not key:
            warnings.append(f"{PRAGMA_PREFIX} token {token!r} has no key before '='")
        elif key in KNOWN_KEYS:
            # Last spelling wins; a repeated key in one comment is a
            # typo either way and the warning-free path keeps the
            # diagram rendering.
            options[key] = value
        elif key in KNOWN_FLAGS:
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

    @property
    def is_empty(self) -> bool:
        return self.leaf is None and self.label is None


@dataclass(frozen=True)
class InstanceHints:
    """Hints attached to one instantiation, keyed by (parent module, name)."""

    collapse: bool | None = None
    hide: bool | None = None
    label: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.collapse is None and self.hide is None and self.label is None


@dataclass(frozen=True)
class HintMap:
    """Resolved hints, keyed the way the graph layer looks them up.

    Modules are keyed by name; instances by ``(parent_module_name,
    instance_name)`` — the pair a :class:`HierNode` can produce from
    itself and its parent without a path-string convention, and the
    same pair the sidecar spells ``"parent.inst"``.
    """

    modules: Mapping[str, ModuleHints] = field(default_factory=dict)
    instances: Mapping[tuple[str, str], InstanceHints] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when no hint would change any output.

        The graceful-degradation gate: an empty map must leave every
        renderer byte-identical to the no-hints case, so
        :func:`apply_hints` short-circuits on it. Warnings alone
        don't make a map non-empty.
        """
        return not self.modules and not self.instances

    def for_module(self, module_name: str) -> ModuleHints | None:
        return self.modules.get(module_name)

    def for_instance(
        self, parent_module: str, instance_name: str
    ) -> InstanceHints | None:
        return self.instances.get((parent_module, instance_name))


@dataclass(frozen=True)
class _Decl:
    """A pragma-attachable declaration recovered from the module table."""

    line: int
    is_instance: bool
    module: str  # defining module, or the parent module for an instance
    instance: str | None


def _declaration_index(table: ModuleTable) -> dict[str, tuple[_Decl, ...]]:
    """Per-file, line-sorted declarations a pragma can bind to.

    Ties sort instances before modules so a same-line collision (a
    one-line module wrapping an instantiation) resolves to the more
    specific target deterministically.
    """
    per_file: dict[str, list[_Decl]] = {}
    for module in table.modules_by_name.values():
        loc = module.location
        if loc is not None and loc.start_line is not None:
            per_file.setdefault(loc.file, []).append(
                _Decl(
                    line=loc.start_line,
                    is_instance=False,
                    module=module.name,
                    instance=None,
                )
            )
        for inst in module.instances:
            iloc = inst.location
            if iloc is None or iloc.start_line is None:
                continue
            per_file.setdefault(iloc.file, []).append(
                _Decl(
                    line=iloc.start_line,
                    is_instance=True,
                    module=module.name,
                    instance=inst.name,
                )
            )
    return {
        file: tuple(
            sorted(
                decls,
                key=lambda d: (d.line, not d.is_instance, d.module, d.instance or ""),
            )
        )
        for file, decls in sorted(per_file.items())
    }


def _associate(pragma: Pragma, decls: Sequence[_Decl]) -> _Decl | None:
    """Bind ``pragma`` to a declaration per the phase-1 rules.

    - Trailing pragma (code to its left): the declaration *starting*
      on that line. A trailing pragma on a non-declaration line binds
      to nothing — falling through to the next declaration would make
      a stray comment silently reshape a diagram several lines away.
    - Standalone pragma: the nearest declaration starting below it in
      the same file.
    """
    if not pragma.own_line:
        for decl in decls:
            if decl.line == pragma.line:
                return decl
        return None
    for decl in decls:
        if decl.line > pragma.line:
            return decl
    return None


def _describe(decl: _Decl) -> str:
    if decl.is_instance:
        return f"instance {decl.instance!r} in module {decl.module!r}"
    return f"module {decl.module!r}"


def resolve_hints(pragmas: Sequence[Pragma], table: ModuleTable) -> HintMap:
    """Bind scanned pragmas to modules / instances in ``table``.

    Wrong-target-kind use (``leaf`` on an instance, ``collapse`` on a
    module) is a warning, not an error: the intent is legible, the
    placement isn't, and the message says where it belongs.
    """
    warnings: list[str] = []
    modules: dict[str, ModuleHints] = {}
    instances: dict[tuple[str, str], InstanceHints] = {}
    index = _declaration_index(table)

    for pragma in pragmas:
        where = f"{pragma.file}:{pragma.line}"
        warnings.extend(f"{where}: {w}" for w in pragma.payload.warnings)
        if pragma.payload.is_empty:
            continue
        decl = _associate(pragma, index.get(pragma.file, ()))
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
            continue

        label = pragma.option("label")
        if decl.is_instance:
            assert decl.instance is not None
            key = (decl.module, decl.instance)
            current = instances.get(key, InstanceHints())
            for flag in pragma.flags:
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
            instances[key] = current
        else:
            current_m = modules.get(decl.module, ModuleHints())
            for flag in pragma.flags:
                if flag in INSTANCE_ONLY_FLAGS:
                    warnings.append(
                        f"{where}: {flag!r} applies to an instance, not "
                        f"{_describe(decl)}; move it onto the instantiation "
                        f"(or use 'leaf' here)"
                    )
                elif flag == "leaf":
                    current_m = replace(current_m, leaf=True)
            if label is not None:
                current_m = replace(current_m, label=label)
            modules[decl.module] = current_m

    return HintMap(
        modules={k: v for k, v in sorted(modules.items()) if not v.is_empty},
        instances={k: v for k, v in sorted(instances.items()) if not v.is_empty},
        warnings=tuple(warnings),
    )


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
        current = modules.get(name, ModuleHints())
        modules[name] = ModuleHints(
            leaf=current.leaf if hints.leaf is None else hints.leaf,
            label=current.label if hints.label is None else hints.label,
        )
    instances = dict(base.instances)
    for key, inst_hints in override.instances.items():
        current_i = instances.get(key, InstanceHints())
        instances[key] = InstanceHints(
            collapse=(
                current_i.collapse
                if inst_hints.collapse is None
                else inst_hints.collapse
            ),
            hide=current_i.hide if inst_hints.hide is None else inst_hints.hide,
            label=current_i.label if inst_hints.label is None else inst_hints.label,
        )
    return HintMap(
        modules=dict(sorted(modules.items())),
        instances=dict(sorted(instances.items())),
        warnings=base.warnings + override.warnings,
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


def parse_hint_sidecar(payload: object, *, source: str) -> HintMap:
    """Validate a decoded sidecar document into a :class:`HintMap`.

    Split from :func:`load_hint_sidecar` so the schema rules are
    testable without touching a filesystem. Unknown *entry* keys warn
    (same vocabulary-drift tolerance as the pragma parser); unknown
    top-level keys are ignored (forward compatibility, matching the
    clock-domain-map consumer rules); wrong *types* raise, because a
    sidecar is an artefact the user deliberately pointed at.
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
        parts = key_text.split(".")
        if len(parts) != 2 or not all(parts):
            raise HintsError(f"{where}: key must be '<parent_module>.<instance_name>'")
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
            else:
                warnings.append(f"{where}: {_unknown_word_warning(name)}")
        if not inst_hints.is_empty:
            instances[(parts[0], parts[1])] = inst_hints

    return HintMap(modules=modules, instances=instances, warnings=tuple(warnings))


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
    if label is None and children == node.children:
        return node
    return replace(node, children=children, display_label=label)
