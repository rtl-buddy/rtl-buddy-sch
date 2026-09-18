"""Loader + hierarchy join for the physical overlay (Phase 7b — #22).

Consumes the structured physical model ``rb synth`` and ``rb power``
publish per run (rtl-buddy/rtl_buddy#558, delivering
rtl-buddy/rtl_buddy#114): ``phys-model.json`` plus the
``phys-manifest.json`` that discovers it. ``--overlay phys=PATH``
accepts either — a manifest is followed through its ``model``
pointer, a model is read directly.

The document has two independently-produced halves and this module
reads each one in its own namespace, because the producer spells
``module`` in two of them (rtl_buddy's ``docs/concepts/phys.md``,
"What the module join can answer"):

- ``modules`` — one row per **RTL module** as Yosys' ``stat -json``
  saw it, carrying ``cell_count`` and ``area_um2``. Two caveats
  that shape everything below: ``area_um2`` *already includes* the
  submodules' area while ``cell_count`` does not, so summing areas
  across a hierarchy double-counts, and the area of a module is
  therefore already a subtree figure.
- ``instances`` — one row per **leaf instance** of the mapped
  netlist, carrying the four power columns. Its ``module`` field is
  the *Liberty cell* the leaf is an instance of (``DFF_X1``), not an
  RTL module, and its ``instance_path`` is rootless — relative to
  the model's ``design.top``, in the producing tool's own spelling
  (``u_sub/_64_``).

So there are exactly two joins, and they are different joins:

**Area / cells — RTL name to RTL name.** A view node's
``module_name`` against the ``modules`` row of the same name. This
is the join rtl_buddy's own `rb phys module` makes, and the one the
schematic can make *correctly* where a flat table cannot: the
schematic owns the elaborated hierarchy, so it knows which node a
module row belongs to. The instance rows' Liberty ``module`` names
are joined to **nothing** — a design with an RTL module called
``DFF_X1`` would otherwise collect every flop's power, which is
exactly the naive join rtl-buddy/rtl_buddy#558's review removed.

**Power — rootless instance path to instance path.** Each leaf row
is rooted the way the hub's ``/phy`` pane roots one before putting
it on the wire (prepend the design top, unconditionally; see
:func:`rooted_model_path`), then attributed to its **nearest
enclosing hierarchy node** — the deepest view node the path lies
under. A mapped netlist's leaves are cells, which the module-level
view has no node for, so ``u_sub/_64_`` lands on ``u_sub``. Parents
then carry the subtree sum of everything below them; roll-up is the
consumer's job by the model's own rule, and this is the consumer.

**Wrapper tops.** The model's top and the view's root need not be
the same module: a TB-rooted render (``--tb-top``) puts a testbench
above the synthesised design, and a project may synthesise a
partition. The join locates the model top's *instance* in the view
and roots the power join there. When the view holds no instance of
it, the overlay attaches nothing at all and says so in
``overlay_meta.phys`` — a power figure hung on the wrong hierarchy
is worse than an absent one.

**Half-filled models.** A synthesis fills ``modules`` and leaves
``instances`` null; a power run does the reverse. Either is a
warning, never a failure: the overlay contributes the half that is
there and records in ``overlay_meta.phys.notes`` which command
produces the other. Only a ``schema_version`` this module does not
speak is fatal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from rtl_buddy_view.graph import HierNode

#: The overlay's own ``MAJOR.MINOR``, describing the artefact shape it
#: consumes rather than the code. The producer versions the two
#: documents with plain integers (:data:`MODEL_SCHEMA_VERSION` /
#: :data:`MANIFEST_SCHEMA_VERSION`); this is the string
#: ``--list-overlays`` prints, following the convention every other
#: built-in keeps.
SCHEMA_VERSION = "1.0"

#: ``phys-model.json``'s ``schema_version``. A document declaring
#: anything else is refused — the model's halves are dereferenced by
#: shape, so a future incompatible layout would be misread rather
#: than noticed.
MODEL_SCHEMA_VERSION = 1

#: ``phys-manifest.json``'s ``schema_version``. Refused on mismatch
#: for the same reason: the ``model`` pointer is what we follow.
MANIFEST_SCHEMA_VERSION = 1

#: Canonical filenames, so a directory can be pointed at instead of
#: either document.
MODEL_FILENAME = "phys-model.json"
MANIFEST_FILENAME = "phys-manifest.json"

#: The units the producer records and this overlay assumes. A model
#: declaring anything else is a warning, not a failure — the numbers
#: still describe the design, they are just in a scale the legend
#: labels wrongly.
EXPECTED_UNITS = {"area": "um2", "power": "uW"}

#: The four power columns a leaf row carries, in the model's spelling.
#: ``dynamic_uw`` is deliberately absent: no producer writes it (it is
#: internal + switching, summed by the consumer), same as the ``/phy``
#: pane's ``dynamic`` metric.
POWER_KEYS = ("leakage_uw", "internal_uw", "switching_uw", "total_uw")

#: The separator every path comparison here is made in. Which of the
#: two a model row is spelled with is the producing tool's choice, so
#: levelling both sides is the only way a rootless OpenSTA path finds
#: the dot-separated view node it belongs under. Emitted values keep
#: their own spelling; this is a comparison rule, not a rewrite.
_CANONICAL_SEPARATOR = "/"

_PATH_SEPARATORS = ("/", ".")

#: What opens a Verilog escaped identifier. ``\gen[0].u_x`` is one
#: name whose ``.`` is not a level; splitting it would invent a parent
#: that does not exist. Only a backslash at the start of a segment
#: leads an escape.
_ESCAPE_LEAD = "\\"

#: Decimal places every emitted figure is rounded to. Float addition
#: is order-sensitive, and the renderers' byte-determinism contract
#: does not survive ``1.0000000000000002`` appearing in one run and
#: not the next; rows are summed in sorted order *and* the result is
#: quantised. 1e-6 µW is a picowatt — far below what any producer
#: reports.
_ROUND = 6


class PhysAnnotationsError(ValueError):
    """Raised when a physical model can't be loaded.

    Its own subclass, like every other overlay loader's error, so the
    CLI can name the failing artefact when several ``--overlay`` flags
    were supplied.
    """


@dataclass(frozen=True)
class ModuleRow:
    """One ``modules`` row: an RTL module as the synthesis saw it.

    ``area_um2`` rolls the module's submodules up; ``cell_count``
    does not (it counts a submodule *instance* as one cell). The two
    columns are therefore not on the same footing, which is why this
    module never sums areas and never reports a subtree cell count.
    """

    name: str
    cell_count: int | None
    area_um2: float | None


@dataclass(frozen=True)
class InstanceRow:
    """One ``instances`` row: a leaf instance of the mapped netlist.

    ``instance_path`` is rootless — relative to the model's
    ``design.top`` — and ``module`` is the Liberty cell, joined to
    nothing (see the module docstring).
    """

    instance_path: str
    module: str | None
    leakage_uw: float
    internal_uw: float
    switching_uw: float
    total_uw: float

    @property
    def dynamic_uw(self) -> float:
        return self.internal_uw + self.switching_uw


@dataclass(frozen=True)
class PhysModel:
    """A parsed ``phys-model.json``, plus where it was read from.

    ``notes`` carries the sentences a surface should show the user —
    a half the run did not produce, units that are not the expected
    ones. They are warnings by construction: the loader raises only
    on a schema version it cannot read.
    """

    source: str
    manifest: str | None
    top: str | None
    modules: dict[str, ModuleRow]
    instances: tuple[InstanceRow, ...]
    totals: dict[str, float | int | None]
    units: dict[str, str]
    publication: str | None
    notes: tuple[str, ...] = ()

    @property
    def has_modules(self) -> bool:
        return bool(self.modules)

    @property
    def has_instances(self) -> bool:
        return bool(self.instances)

    @property
    def is_empty(self) -> bool:
        """True when neither half carries a row.

        The overlay's graceful-degradation gate: an empty model must
        leave every renderer's output exactly as it was.
        """
        return not self.modules and not self.instances


@dataclass
class PhysJoin:
    """The model projected onto one hierarchy.

    ``blocks`` maps a view node's ``instance_path`` to the
    ``node.overlays.phys`` block for it; ``meta`` is the
    ``overlay_meta.phys`` envelope block. ``attached`` is False when
    the join found no anchor for the model's top — then ``blocks`` is
    empty and ``meta.notes`` says why.
    """

    model: PhysModel
    blocks: dict[str, dict] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    attached: bool = False

    def block(self, instance_path: str) -> dict | None:
        """This node's ``overlays.phys`` block, or ``None``."""
        return self.blocks.get(instance_path)

    @property
    def is_empty(self) -> bool:
        return not self.blocks


# --- path levelling ---------------------------------------------------------


def level_path(path: str) -> str:
    r"""One instance path in the separator every comparison here uses.

    Every ``/`` and every ``.`` is a level, whichever the producer
    wrote — except inside an escaped identifier, which runs
    atomically from its leading backslash to the whitespace that ends
    it (or to the end of the path). This is ``level_path`` in
    rtl_buddy's ``phys/query.py`` and ``toWirePath`` in its ``/phy``
    pane, in the model's separator rather than the wire's; the three
    are copies on either side of one contract and must not drift.
    """
    text = str(path)
    out: list[str] = []
    at_segment_start = True
    index = 0
    while index < len(text):
        char = text[index]
        if at_segment_start and char == _ESCAPE_LEAD:
            end = index
            while end < len(text) and not text[end].isspace():
                end += 1
            out.append(text[index:end])
            while end < len(text) and text[end].isspace():
                end += 1
            index = end
            at_segment_start = False
            continue
        if char in _PATH_SEPARATORS:
            out.append(_CANONICAL_SEPARATOR)
            at_segment_start = True
        else:
            out.append(char)
            at_segment_start = False
        index += 1
    return "".join(out)


def rooted_model_path(rootless: str, root: str) -> str:
    """A model row's path, rooted at ``root``, levelled.

    Unconditional, exactly as the ``/phy`` pane's ``withTop`` is and
    for its reason: what this is handed is always a model row, and a
    prefix test would misread the rootless row ``cpu/alu`` under a
    top *named* ``cpu`` as already rooted.
    """
    wire = level_path(rootless)
    stem = level_path(root)
    if not stem:
        return wire
    if not wire:
        return stem
    return f"{stem}{_CANONICAL_SEPARATOR}{wire}"


# --- loading ----------------------------------------------------------------


def load_phys_model(path: Path) -> PhysModel:
    """Load ``path`` into a :class:`PhysModel`.

    ``path`` may be a ``phys-manifest.json`` (followed through its
    ``model`` pointer), a ``phys-model.json``, or a directory holding
    either — the manifest first, since that is the discovery contract.

    Raises :class:`PhysAnnotationsError` on an unreadable or
    non-object document, on a ``schema_version`` this module does not
    speak, and on a manifest whose ``model`` pointer resolves to
    nothing.
    """
    path = Path(path)
    if path.is_dir():
        for candidate in (path / MANIFEST_FILENAME, path / MODEL_FILENAME):
            if candidate.is_file():
                return load_phys_model(candidate)
        raise PhysAnnotationsError(
            f"no {MANIFEST_FILENAME} or {MODEL_FILENAME} in directory: {path}"
        )

    document = _read_json(path)
    if _is_manifest(document):
        return _load_via_manifest(path, document)
    return _model_from_document(document, source=path, manifest=None)


def _read_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PhysAnnotationsError(f"cannot read {path}: {e}") from e
    try:
        document = json.loads(text)
    except ValueError as e:
        raise PhysAnnotationsError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(document, dict):
        raise PhysAnnotationsError(
            f"{path}: expected a JSON object at the document root, "
            f"got {type(document).__name__}"
        )
    return document


def _is_manifest(document: dict) -> bool:
    """Tell a manifest from a model by what only a manifest has.

    ``model`` is the manifest's pointer at the other document and the
    model has no such key, so the discrimination needs no filename —
    a user who renamed either file still gets the right reader. The
    two halves' block names (``synth`` / ``power``) are *also*
    manifest-only, but they are null in a half-filled document, so
    the pointer is the one to key on.
    """
    return "model" in document and "modules" not in document


def _load_via_manifest(manifest_path: Path, manifest: dict) -> PhysModel:
    _require_schema_version(
        manifest, MANIFEST_SCHEMA_VERSION, manifest_path, kind="phys-manifest"
    )
    pointer = manifest.get("model")
    if pointer is not None and not isinstance(pointer, str):
        raise PhysAnnotationsError(
            f"{manifest_path}: 'model' must be a path string, "
            f"got {type(pointer).__name__}"
        )
    model_path = _resolve_model_path(manifest_path, manifest, pointer)
    model = _model_from_document(
        _read_json(model_path), source=model_path, manifest=manifest_path
    )
    return _cross_check_publication(model, manifest)


def _resolve_model_path(
    manifest_path: Path, manifest: dict, pointer: str | None
) -> Path:
    """Find the model a manifest points at.

    Manifest paths are **project-root-relative**, not
    manifest-relative — the producer's own rule, so a manifest
    survives being archived or read from elsewhere. But the resolution
    order here does not start from that rule, and deliberately:

    1. an **absolute** pointer, which is unambiguous;
    2. the pointer's basename **beside the manifest**, then the
       canonical ``phys-model.json`` there. Every producer writes the
       two documents into one directory, and that is the one pairing
       that cannot be wrong: two documents in one place are one
       publication, whatever the project root turned out to be;
    3. the **project-root-relative** pointer, with the root recovered
       the way rtl_buddy's ``manifest.project_root_for`` does — count
       the components of the manifest's own ``phys_dir`` back up from
       the directory it was found in;
    4. the pointer **relative to the cwd**, the last resort.

    Reversing (2) and (3) — which is how this was first written — puts
    a *counted* root ahead of an observed fact. The count is only
    right when the manifest is read through the same route it was
    written through, and an ``artefacts/`` symlinked to scratch is an
    ordinary layout that breaks exactly that (the producer's own
    ``project_root_for`` documents the case). Getting it wrong there
    does not fail: it silently resolves to *another run's* model that
    happens to sit at the same project-relative path, which is a wrong
    answer where an unfound file would have been an honest one.
    """
    candidates: list[Path] = []
    if pointer:
        as_given = Path(pointer)
        if as_given.is_absolute():
            candidates.append(as_given)
        else:
            candidates.append(manifest_path.parent / as_given.name)
            candidates.append(manifest_path.parent / MODEL_FILENAME)
            phys_dir = manifest.get("phys_dir")
            if (
                isinstance(phys_dir, str)
                and phys_dir
                and not Path(phys_dir).is_absolute()
            ):
                root = manifest_path.parent
                for _ in Path(phys_dir).parts:
                    root = root.parent
                candidates.append(root / as_given)
            candidates.append(as_given)
    candidates.append(manifest_path.parent / MODEL_FILENAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    named = pointer if pointer else "(null)"
    raise PhysAnnotationsError(
        f"{manifest_path}: the model it points at was not found "
        f"(model={named!r}); tried {[str(c) for c in candidates]}"
    )


def _cross_check_publication(model: PhysModel, manifest: dict) -> PhysModel:
    """Note a model+manifest pair that was not written together.

    Both documents of one publication carry the same token; a
    mismatch means the pair was caught mid-rewrite or the manifest
    is stale. The producer's readers re-read; an overlay cannot (the
    user named a path on a command line), so it reports the numbers
    it has and says they may belong to a different publication than
    the manifest describes.

    An **unpaired** pair — exactly one of the two tokens present — is
    its own note, and the producer treats the same state as a refusal:
    ``phys.publish._existing_pair`` merges onto neither document when
    it cannot show the two were written together, because an unpaired
    manifest is evidence that the last publish did not finish. A
    consumer cannot refuse (the user named the path), so it reads the
    documents and says the pairing is unproven.
    """
    theirs = manifest.get("publication")
    ours = model.publication
    mine_is_token = isinstance(ours, str)
    theirs_is_token = isinstance(theirs, str)
    if mine_is_token and theirs_is_token and theirs != ours:
        return _with_note(
            model,
            "the manifest and the model carry different publication tokens: "
            "one of the pair is from an earlier run (re-read after the "
            "producing command finishes)",
        )
    if mine_is_token != theirs_is_token:
        missing = "model" if theirs_is_token else "manifest"
        return _with_note(
            model,
            f"unpaired publication: the {missing} carries no publication "
            f"token, so there is no evidence these two documents were "
            f"written together — the run that wrote them may not have "
            f"finished publishing",
        )
    return model


def _with_note(model: PhysModel, note: str) -> PhysModel:
    return PhysModel(
        source=model.source,
        manifest=model.manifest,
        top=model.top,
        modules=model.modules,
        instances=model.instances,
        totals=model.totals,
        units=model.units,
        publication=model.publication,
        notes=model.notes + (note,),
    )


def _require_schema_version(
    document: dict, expected: int, path: Path, *, kind: str
) -> None:
    """Refuse a document whose declared schema version isn't ours.

    Compared exactly rather than on a major, because the producer
    versions both documents with a single integer that is bumped only
    for an incompatible change — there is no minor to tolerate. An
    absent version is refused too: every document the producer has
    ever written carries one, so its absence means this is not the
    document it was taken for.
    """
    declared = document.get("schema_version")
    if declared == expected:
        return
    if declared is None:
        raise PhysAnnotationsError(
            f"{path}: no 'schema_version' key — this does not look like a "
            f"{kind} document (expected schema_version {expected})"
        )
    raise PhysAnnotationsError(
        f"{path}: unsupported {kind} schema_version {declared!r} "
        f"(this overlay reads {expected}); regenerate the artefacts with a "
        f"matching rtl-buddy, or upgrade rtl-buddy-sch"
    )


def _model_from_document(
    document: dict, *, source: Path, manifest: Path | None
) -> PhysModel:
    _require_schema_version(document, MODEL_SCHEMA_VERSION, source, kind="phys-model")
    notes: list[str] = []

    design = document.get("design")
    if design is not None and not isinstance(design, dict):
        raise PhysAnnotationsError(
            f"{source}: 'design' must be an object, got {type(design).__name__}"
        )
    top = (design or {}).get("top")
    if top is not None and not isinstance(top, str):
        raise PhysAnnotationsError(
            f"{source}: 'design.top' must be a string, got {type(top).__name__}"
        )

    units = _units(document, source, notes)
    modules = _module_rows(document, source, notes)
    instances = _instance_rows(document, source, notes)
    totals = _totals(document, source)

    if top is None:
        notes.append(
            "the model records no design top, so the power join is rooted at "
            "the rendered root; a wrapper above the synthesised design would "
            "misplace every leaf row"
        )

    return PhysModel(
        source=str(source),
        manifest=None if manifest is None else str(manifest),
        top=top,
        modules=modules,
        instances=instances,
        totals=totals,
        units=units,
        publication=_opt_str(document.get("publication")),
        notes=tuple(notes),
    )


def _units(document: dict, source: Path, notes: list[str]) -> dict[str, str]:
    raw = document.get("units")
    if raw is None:
        return dict(EXPECTED_UNITS)
    if not isinstance(raw, dict):
        raise PhysAnnotationsError(
            f"{source}: 'units' must be an object, got {type(raw).__name__}"
        )
    units = {
        key: str(raw.get(key, EXPECTED_UNITS[key])) for key in sorted(EXPECTED_UNITS)
    }
    for key, expected in sorted(EXPECTED_UNITS.items()):
        if units[key] != expected:
            notes.append(
                f"the model reports {key} in {units[key]!r}, not {expected!r}: "
                f"the numbers are carried through unchanged, so every label "
                f"this overlay draws names the wrong unit"
            )
    return units


def _module_rows(
    document: dict, source: Path, notes: list[str]
) -> dict[str, ModuleRow]:
    """The ``modules`` half, keyed by RTL module name.

    ``null`` is the producer's "this run did not produce it", so it
    is a note rather than an error. A duplicate name keeps the first
    row and notes the collision: two rows for one module means the
    document was assembled from two designs, and picking silently
    would make the fill depend on dict order.
    """
    raw = document.get("modules")
    if raw is None:
        notes.append(
            "this model carries no module rows (area and cell counts): run "
            "`rb synth` into the same artefact directory to fill them"
        )
        return {}
    if not isinstance(raw, list):
        raise PhysAnnotationsError(
            f"{source}: 'modules' must be an array or null, got {type(raw).__name__}"
        )
    rows: dict[str, ModuleRow] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise PhysAnnotationsError(
                f"{source}: modules[{index}] must be an object, "
                f"got {type(entry).__name__}"
            )
        # ``module`` is the producer's spelling (`phys/reports.py`);
        # ``name`` is accepted as an alias so a hand-written document
        # keyed the obvious way still joins.
        name = entry.get("module", entry.get("name"))
        if not isinstance(name, str) or not name:
            raise PhysAnnotationsError(
                f"{source}: modules[{index}] has no 'module' name"
            )
        if name in rows:
            notes.append(
                f"the model carries two rows for module {name!r}; the first "
                f"is used and the second ignored"
            )
            continue
        rows[name] = ModuleRow(
            name=name,
            cell_count=_opt_int(entry.get("cell_count"), source, f"modules[{index}]"),
            area_um2=_opt_float(entry.get("area_um2"), source, f"modules[{index}]"),
        )
    return rows


def _instance_rows(
    document: dict, source: Path, notes: list[str]
) -> tuple[InstanceRow, ...]:
    """The ``instances`` half, sorted by path.

    Sorted on the way in so every sum below is made in one order —
    float addition is not associative and the renderers' output has
    to be byte-stable across runs.
    """
    raw = document.get("instances")
    if raw is None:
        notes.append(
            "this model carries no instance rows (power): run `rb power` "
            "into the same artefact directory to fill them"
        )
        return ()
    if not isinstance(raw, list):
        raise PhysAnnotationsError(
            f"{source}: 'instances' must be an array or null, got {type(raw).__name__}"
        )
    rows: list[InstanceRow] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise PhysAnnotationsError(
                f"{source}: instances[{index}] must be an object, "
                f"got {type(entry).__name__}"
            )
        path = entry.get("instance_path")
        if not isinstance(path, str) or not path:
            raise PhysAnnotationsError(
                f"{source}: instances[{index}] has no 'instance_path'"
            )
        where = f"instances[{index}]"
        rows.append(
            InstanceRow(
                instance_path=path,
                module=_opt_str(entry.get("module")),
                leakage_uw=_opt_float(entry.get("leakage_uw"), source, where) or 0.0,
                internal_uw=_opt_float(entry.get("internal_uw"), source, where) or 0.0,
                switching_uw=_opt_float(entry.get("switching_uw"), source, where)
                or 0.0,
                total_uw=_opt_float(entry.get("total_uw"), source, where) or 0.0,
            )
        )
    return tuple(sorted(rows, key=lambda row: level_path(row.instance_path)))


def _totals(document: dict, source: Path) -> dict[str, float | int | None]:
    """The producer's own scraped totals, carried through verbatim.

    Not a derivation and never compared for equality with the
    roll-up: the two come from different scrapes of the same run, so
    a small disagreement is information (the producer's own docstring
    makes the same point). ``cell_count`` stays an int — it is a
    count, and a legend reading ``4.0 cells`` would be this module's
    doing rather than the document's.
    """
    raw = document.get("totals")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PhysAnnotationsError(
            f"{source}: 'totals' must be an object or null, got {type(raw).__name__}"
        )
    totals: dict[str, float | int | None] = {}
    for key, value in sorted(raw.items()):
        if key == "cell_count":
            totals[key] = _opt_int(value, source, "totals")
        else:
            totals[key] = _opt_float(value, source, "totals")
    return totals


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _opt_int(value: object, source: Path, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PhysAnnotationsError(
            f"{source}: {where}.cell_count must be a number or null, "
            f"got {type(value).__name__}"
        )
    return int(value)


def _opt_float(value: object, source: Path, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PhysAnnotationsError(
            f"{source}: {where} carries a non-numeric measurement "
            f"({value!r}); expected a number or null"
        )
    return float(value)


# --- the join ---------------------------------------------------------------


def join_hierarchy(model: PhysModel, root: HierNode) -> PhysJoin:
    """Project ``model`` onto the hierarchy rooted at ``root``.

    Returns a :class:`PhysJoin` whose ``blocks`` are ready to land
    under ``node.overlays.phys`` and whose ``meta`` is the
    ``overlay_meta.phys`` envelope block. Pure: nothing on the graph
    is mutated, which keeps the analyzer's "chain of pure functions"
    rule and lets the tree renderer and the JSON renderer share one
    join.
    """
    nodes = tuple(_walk(root))
    anchor, anchor_note = _anchor_for(model, root, nodes)

    self_power: dict[str, list[float]] = {n.instance_path: [0.0] * 4 for n in nodes}
    self_leaves: dict[str, int] = {n.instance_path: 0 for n in nodes}
    matched = 0
    unmatched: list[str] = []

    if anchor is not None:
        owners = _owner_index(nodes)
        for row in model.instances:
            owner = _owner_of(rooted_model_path(row.instance_path, anchor), owners)
            if owner is None:
                unmatched.append(row.instance_path)
                continue
            matched += 1
            self_leaves[owner] += 1
            bucket = self_power[owner]
            bucket[0] += row.leakage_uw
            bucket[1] += row.internal_uw
            bucket[2] += row.switching_uw
            bucket[3] += row.total_uw

    subtree_power, subtree_leaves = _rollup(root, self_power, self_leaves)

    # Both channels are gated on the anchor, not just the power one.
    # A module row joins by NAME, so a model of another design that
    # happens to share a module name would otherwise paint area on it
    # while ``attached: false`` and the notes said nothing was
    # attached — a block reading "measured" from a measurement of a
    # different design, contradicted by its own envelope.
    attached = anchor is not None
    blocks: dict[str, dict] = {}
    for node in nodes:
        block = _node_block(
            node,
            model,
            self_power[node.instance_path],
            subtree_power[node.instance_path],
            self_leaves[node.instance_path],
            subtree_leaves[node.instance_path],
            has_area=attached,
            has_power=attached and model.has_instances,
        )
        if block:
            blocks[node.instance_path] = block

    # Module rows no node claimed. The common cause is not an error at
    # all: Yosys names a parameterized module ``$paramod\sub\W=32``,
    # which matches no RTL module name the view carries, so its area
    # silently goes missing. Counting the rows is how a surface can say
    # "some area is unaccounted for" instead of showing a diagram whose
    # blocks quietly have none.
    claimed = {n.module_name for n in nodes} if attached else set()
    unmatched_modules = sorted(name for name in model.modules if name not in claimed)

    meta = _meta(
        model,
        root=root,
        anchor=anchor,
        anchor_note=anchor_note,
        rollup=subtree_power.get(root.instance_path, [0.0] * 4),
        matched=matched,
        unmatched=unmatched,
        unmatched_modules=unmatched_modules,
    )
    return PhysJoin(model=model, blocks=blocks, meta=meta, attached=attached)


def _walk(node: HierNode) -> Iterable[HierNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _anchor_for(
    model: PhysModel, root: HierNode, nodes: tuple[HierNode, ...]
) -> tuple[str | None, str | None]:
    """The view instance path the model's rootless rows hang off.

    Three cases, in order:

    - the rendered root *is* the model's top (the ordinary
      DUT-rooted render, and the one where nothing has to be
      inferred);
    - the model's top is instantiated somewhere in the view — a
      testbench wrapper, or a partition synthesised on its own.
      The shallowest instance wins, alphabetically among equals, so
      the choice is deterministic; more than one is reported,
      because a design instantiated twice has two sets of leaves and
      the model describes one of them;
    - the model's top is nowhere in the view. Then there is no
      anchor: the overlay attaches nothing rather than hanging one
      design's watts on another's hierarchy.

    A model with no recorded top falls back to the rendered root,
    with the note the loader already added.
    """
    if model.top is None:
        return root.instance_path, None
    if root.module_name == model.top:
        return root.instance_path, None
    matches = sorted(
        (n.instance_path for n in nodes if n.module_name == model.top),
        key=lambda path: (len(level_path(path).split(_CANONICAL_SEPARATOR)), path),
    )
    if not matches:
        return None, (
            f"the model's top {model.top!r} is not this design: no instance of "
            f"it exists under {root.module_name!r}, so no area or power was "
            f"attached. Render the synthesised design (or its testbench) to "
            f"see the overlay."
        )
    note = None
    if len(matches) > 1:
        note = (
            f"{model.top!r} is instantiated {len(matches)} times here "
            f"({', '.join(matches)}); the model describes one of them and the "
            f"join is rooted at {matches[0]}"
        )
    return matches[0], note


def _owner_index(nodes: tuple[HierNode, ...]) -> dict[str, str]:
    """``levelled view path -> instance_path`` for every node.

    Built once per join: a mapped design's power half runs to six
    figures of leaf rows, and each one walks up from its own depth.
    """
    return {level_path(n.instance_path): n.instance_path for n in nodes}


def _owner_of(rooted: str, owners: dict[str, str]) -> str | None:
    """The nearest enclosing hierarchy node for a rooted leaf path.

    Walks up one level at a time. A leaf of a mapped netlist is a
    cell — ``u_sub/_64_`` — and the module-level view has no node for
    it, so the row belongs to the deepest *scope* above it. A path
    that matches a node exactly is owned by that node (a leaf row
    whose scope is the design top, ``_18_``, resolves the same way
    once rooted).
    """
    candidate = rooted
    while candidate:
        owner = owners.get(candidate)
        if owner is not None:
            return owner
        cut = candidate.rfind(_CANONICAL_SEPARATOR)
        if cut < 0:
            return None
        candidate = candidate[:cut]
    return None


def _rollup(
    node: HierNode,
    self_power: dict[str, list[float]],
    self_leaves: dict[str, int],
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """Bottom-up subtree sums of the per-node self values.

    The model records leaf values only — by its own rule, because a
    subtree sum depends on the hierarchy the consumer projects onto.
    This is that projection, and the whole of it: four columns added
    in a fixed child order.
    """
    subtree_power: dict[str, list[float]] = {}
    subtree_leaves: dict[str, int] = {}

    def visit(current: HierNode) -> None:
        totals = list(self_power[current.instance_path])
        leaves = self_leaves[current.instance_path]
        for child in current.children:
            visit(child)
            child_totals = subtree_power[child.instance_path]
            for index in range(4):
                totals[index] += child_totals[index]
            leaves += subtree_leaves[child.instance_path]
        subtree_power[current.instance_path] = totals
        subtree_leaves[current.instance_path] = leaves

    visit(node)
    return subtree_power, subtree_leaves


def _node_block(
    node: HierNode,
    model: PhysModel,
    own: list[float],
    subtree: list[float],
    own_leaves: int,
    subtree_leaves: int,
    *,
    has_area: bool,
    has_power: bool,
) -> dict:
    """One node's ``overlays.phys`` block.

    Keys are omitted rather than nulled when the half behind them is
    absent, following the coverage overlay: a missing key means "this
    overlay had nothing to say about this node", which the viewer
    renders as "not measured" and never as zero. A node with nothing
    at all gets no block.

    There is deliberately **no self area**. The producer's module area
    already includes the submodules', so a self figure could only be
    derived by subtracting the children's — and the view does not carry
    instance multiplicity, so ``leafm u_arr [3:0]`` is one
    :class:`~rtl_buddy_view.graph.HierNode` where Yosys elaborated
    four. The subtraction would then account for one child where the
    area covers four and report the other three as this scope's own
    gates, silently and with no way to notice: the "negative remainder"
    guard never fires, because over-reporting is the direction the
    error takes. Power has no such problem — a self power figure is the
    leaf rows attributed to this node, counted rather than derived — so
    the self scope is a power-only distinction and the area channel
    keeps the module roll-up in both scopes.
    """
    block: dict[str, float | int | str] = {}
    row = model.modules.get(node.module_name) if has_area else None
    if row is not None:
        if row.cell_count is not None:
            block["cell_count"] = row.cell_count
        if row.area_um2 is not None:
            block["area_um2"] = round(row.area_um2, _ROUND)
    if has_power:
        leakage, internal, switching, total = own
        block["leakage_uw"] = round(leakage, _ROUND)
        block["internal_uw"] = round(internal, _ROUND)
        block["switching_uw"] = round(switching, _ROUND)
        block["dynamic_uw"] = round(internal + switching, _ROUND)
        block["total_uw"] = round(total, _ROUND)
        s_leakage, s_internal, s_switching, s_total = subtree
        block["subtree_leakage_uw"] = round(s_leakage, _ROUND)
        block["subtree_internal_uw"] = round(s_internal, _ROUND)
        block["subtree_switching_uw"] = round(s_switching, _ROUND)
        block["subtree_dynamic_uw"] = round(s_internal + s_switching, _ROUND)
        block["subtree_total_uw"] = round(s_total, _ROUND)
        block["leaf_instances"] = own_leaves
        block["subtree_leaf_instances"] = subtree_leaves
    return block


def _meta(
    model: PhysModel,
    *,
    root: HierNode,
    anchor: str | None,
    anchor_note: str | None,
    rollup: list[float],
    matched: int,
    unmatched: list[str],
    unmatched_modules: list[str],
) -> dict:
    """The ``overlay_meta.phys`` envelope block.

    Everything a surface needs to say what it is showing and how much
    of it it believes: where the numbers came from, which halves the
    run produced, where the join was rooted, the producer's own
    totals beside our roll-up of the rows, and how many rows of each
    kind found no home. ``notes`` is a list of whole sentences — a
    consumer gates on it being non-empty, never on a code.

    ``rollup`` follows the per-node rule (``view-json-v1.md`` §6):
    keys are **omitted, never nulled or zeroed**. A run with no power
    half, and a join that attached nothing, both have no power to roll
    up — and ``total_uw: 0.0`` there would be read as a design that
    burns nothing, which is the one thing it must not say.
    """
    attached = anchor is not None
    notes = list(model.notes)
    if anchor_note:
        notes.append(anchor_note)
    if unmatched:
        shown = sorted(unmatched)[:5]
        notes.append(
            f"{len(unmatched)} instance row(s) matched no scope in this "
            f"hierarchy and are not counted (e.g. {', '.join(shown)}); the "
            f"model was measured on a netlist whose hierarchy differs from "
            f"the RTL as rendered"
        )
    # Only worth saying when something DID attach: with no anchor every
    # module row is unmatched by construction, and the anchor note
    # above has already explained why.
    if unmatched_modules and attached:
        notes.append(
            f"{len(unmatched_modules)} module row(s) matched no node in this "
            f"hierarchy, so their area is not shown "
            f"(e.g. {', '.join(unmatched_modules[:5])}); Yosys names a "
            f"parameterized module '$paramod\\<name>\\<params>', which "
            f"matches no RTL module name the view carries"
        )

    rolled: dict[str, float] = {}
    if attached and model.has_instances:
        rolled["leakage_uw"] = round(rollup[0], _ROUND)
        rolled["internal_uw"] = round(rollup[1], _ROUND)
        rolled["switching_uw"] = round(rollup[2], _ROUND)
        rolled["dynamic_uw"] = round(rollup[1] + rollup[2], _ROUND)
        rolled["total_uw"] = round(rollup[3], _ROUND)
    if anchor is not None:
        root_row = model.modules.get(_module_at(root, anchor))
        if root_row is not None and root_row.area_um2 is not None:
            rolled["area_um2"] = round(root_row.area_um2, _ROUND)

    disagreement = _totals_disagreement(model.totals, rolled)
    if disagreement is not None:
        notes.append(disagreement)

    return {
        "source": model.source,
        "manifest": model.manifest,
        "model_top": model.top,
        "view_root": root.instance_path,
        "join_root": anchor,
        "attached": attached,
        "units": dict(model.units),
        "halves": {
            "modules": model.has_modules,
            "instances": model.has_instances,
        },
        "totals": dict(model.totals),
        "rollup": rolled,
        "matched_instance_rows": matched,
        "unmatched_instance_rows": len(unmatched),
        "unmatched_module_rows": len(unmatched_modules),
        "unmatched_module_names": unmatched_modules[:5],
        "notes": notes,
    }


#: How far the roll-up of the rows may sit from the producer's own
#: scraped total before it is worth saying out loud. The same 0.5% the
#: test suite pins the fixture to (rtl-buddy/rtl-buddy-sch#22's
#: acceptance criterion), checked at runtime as well: the two figures
#: come from different scrapes of one run, so a small gap is expected
#: and a large one means rows went missing or landed twice.
TOTALS_TOLERANCE = 0.005


def _totals_disagreement(
    totals: dict[str, float | int | None], rolled: dict[str, float]
) -> str | None:
    """A sentence when ``totals`` and ``rollup`` disagree materially.

    Both blocks are already in the envelope, so a surface *could*
    compare them itself — but every surface would have to, and the one
    that forgets shows a confident number with rows missing behind it.
    Compared on total power only: it is the column both sides always
    carry, and the one every other power figure is a component of.
    """
    reported = totals.get("total_uw")
    ours = rolled.get("total_uw")
    if not isinstance(reported, (int, float)) or ours is None:
        return None
    if reported == 0:
        return None
    drift = abs(ours - reported) / abs(reported)
    if drift <= TOTALS_TOLERANCE:
        return None
    return (
        f"the roll-up of the instance rows ({ours:.6g} µW) disagrees with the "
        f"total the run itself scraped ({reported:.6g} µW) by "
        f"{drift * 100:.1f}% — more than the {TOTALS_TOLERANCE * 100:g}% two "
        f"scrapes of one run should differ by, so rows are probably missing "
        f"from this hierarchy or counted twice in it"
    )


def _module_at(root: HierNode, instance_path: str) -> str:
    for node in _walk(root):
        if node.instance_path == instance_path:
            return node.module_name
    return root.module_name  # pragma: no cover - anchor comes from the walk


# --- render helpers ---------------------------------------------------------


def tree_suffix(join: PhysJoin | None, node: HierNode) -> str:
    """The ASCII tree's ``  [area=1240µm² P=15µW]`` suffix.

    Empty whenever the overlay contributed nothing for this node, so
    a render with no ``--overlay phys=…`` (or with a model that
    attached nothing) is byte-identical to the Phase-1 baseline —
    the graceful-degradation contract every renderer in this repo
    keeps.

    Both figures are **subtree** figures, because a tree line stands
    for a scope: ``area_um2`` is the producer's own rolled-up module
    area and ``P`` is this overlay's roll-up of the leaf rows below.
    """
    if join is None:
        return ""
    block = join.block(node.instance_path)
    if not block:
        return ""
    parts: list[str] = []
    area = block.get("area_um2")
    if isinstance(area, (int, float)):
        parts.append(f"area={_compact(float(area))}µm²")
    power = block.get("subtree_total_uw")
    if isinstance(power, (int, float)):
        parts.append(f"P={_compact(float(power))}µW")
    if not parts:
        return ""
    return "  [" + " ".join(parts) + "]"


def _compact(value: float) -> str:
    """A figure short enough to sit at the end of a tree line.

    Whole numbers above 100 lose their decimals (an area of
    ``1240.37µm²`` says nothing ``1240µm²`` does not), everything
    else keeps two, and a value that would round to nothing keeps
    three significant digits so a real-but-tiny leakage doesn't
    render as ``0``.
    """
    magnitude = abs(value)
    if magnitude >= 100:
        return f"{value:.0f}"
    if magnitude >= 0.01 or magnitude == 0:
        return f"{value:.2f}".rstrip("0").rstrip(".") or "0"
    return f"{value:.3g}"
