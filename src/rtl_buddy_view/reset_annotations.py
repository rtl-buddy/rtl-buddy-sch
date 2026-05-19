"""Loader for the rtl-buddy-cdc reset-domain map (Phase 3 overlay input).

Consumes the JSON artifact produced by ``rtl-buddy-cdc lint
--emit-reset-domain-map`` (tracked at
[rtl-buddy/rtl-buddy-cdc#108](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/108)).
Schema is owned by rtl-buddy-cdc and documented at
``wiki/raw/articles/rtl-buddy-cdc-reset-domain-map-schema.md``; this
module is the consumer side and validates the version field on load
so an incompatible producer fails loudly instead of silently
misrendering.

Parallel to :mod:`rtl_buddy_view.annotations` (the clock-domain map
loader); the two artefacts share a ``generator`` / ``design``
envelope but have independent collections, version constants, and
``AnnotationsError`` subclasses so a consumer can compose them
without one bleeding into the other.

Phase 3 scope:

- Load + structurally validate the v1.0 schema.
- Surface the parsed model to renderers.
- Graceful no-reset degradation: when the design has no reset-bearing
  flops the map still parses cleanly with empty collections, and
  downstream renderers fall back to the un-annotated Phase 1/2
  behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rtl_buddy_view.extractor import SourceLocation

#: Highest schema major.minor we know how to consume. Bumping to a
#: new major version is a breaking change; minor bumps must remain
#: backward-compatible from the producer side per the schema
#: contract pinned in rtl-buddy-cdc#108. We accept any 1.x payload
#: today; producers MAY add fields freely as long as they don't
#: change the existing types/keys.
SUPPORTED_SCHEMA_MAJOR = 1


class ResetAnnotationsError(ValueError):
    """Raised when a reset-domain-map payload can't be loaded.

    Distinct from :class:`rtl_buddy_view.annotations.AnnotationsError`
    so a consumer wiring both ``--cdc-annotations`` and
    ``--rdc-annotations`` can surface separate error messages instead
    of conflating the two artefacts.
    """


ResetSourceKind = Literal["port", "inferred", "constant"]
ResetKind = Literal["sync", "async"]
ResetPolarity = Literal["high", "low"]
# ``comb`` only appears on the *consumer* end of crossings/flop_resets,
# never as a top-level ``reset_sources[].source``. Producer schema doc:
# "``comb`` sources are intentionally omitted — they're surfaced in
# ``reset_crossings`` instead."
ResetConsumerKind = Literal["port", "inferred", "constant", "comb"]
ResetCrossingKind = Literal[
    "async-deassert", "polarity-mismatch", "sync-crossing", "comb-driven"
]


@dataclass(frozen=True)
class ResetSource:
    name: str
    source: ResetSourceKind
    polarity: ResetPolarity
    type: ResetKind
    # v1.0 always emits ``None`` here — population is gated on a
    # future producer-side reset-context PR. Consumers should treat
    # ``None`` as "unknown sampling clock" rather than "no clock".
    clock: str | None
    via_synchronizer: bool
    # Only present when ``source == "port"`` and the port carries a
    # ``(* reset_polarity *)`` attribute. Disagreement with
    # ``polarity`` is what produces a ``polarity-mismatch`` crossing.
    declared_polarity: ResetPolarity | None
    location: SourceLocation | None


@dataclass(frozen=True)
class ResetSynchronizer:
    """A flop in the recognized reset-synchronizer set.

    Producer pins this as the *flat* set of member cells — a chain
    view (head/tail/depth) is a v1.x backward-compatible extension,
    not part of v1.0. Renderers that want a "synchronizer chain"
    overlay should infer it from membership + ``dest_clock`` for now.
    """

    instance_path: str
    dest_clock: str | None
    async_in: str | None
    async_in_kind: ResetConsumerKind | None
    location: SourceLocation | None


@dataclass(frozen=True)
class FlopReset:
    """One flop's reset binding — the reset inventory entry."""

    instance_path: str
    clock: str | None
    reset: str
    reset_kind: ResetConsumerKind
    polarity: ResetPolarity
    type: ResetKind
    location: SourceLocation | None


@dataclass(frozen=True)
class ResetCrossing:
    """A structural reset crossing the RDC rule pack would flag.

    Rule severity, waiver suppression, and user-facing messages live
    in the findings report — the map is the structural truth, so we
    deliberately don't carry ``rule`` or ``waived`` fields here.
    """

    instance_path: str
    kind: ResetCrossingKind
    flop_clock: str | None
    reset: str
    reset_kind: ResetConsumerKind
    polarity: ResetPolarity
    type: ResetKind
    location: SourceLocation | None


@dataclass(frozen=True)
class ResetDomainMap:
    schema_version: str
    generator_name: str
    generator_version: str
    design_top: str
    design_frontend: str
    reset_sources: tuple[ResetSource, ...] = ()
    reset_synchronizers: tuple[ResetSynchronizer, ...] = ()
    flop_resets: tuple[FlopReset, ...] = ()
    reset_crossings: tuple[ResetCrossing, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when the design had no reset-bearing flops.

        Phase 3 renderers fall back to un-annotated rendering when
        the map is "empty" in this sense rather than drawing reset
        decoration on a design that has nothing to decorate.
        """
        return not self.flop_resets

    def crossings_into(self, instance_path: str) -> tuple[ResetCrossing, ...]:
        """Crossings whose destination flop is exactly ``instance_path``.

        Unlike the clock map, the reset map has no synth-vs-source
        instance-path duality in v1.0 — the producer emits a single
        ``instance_path`` field per crossing. Renderers that need to
        bucket synth-internal flops back to a source instance should
        do it at the join layer, not here.
        """
        return tuple(
            c for c in self.reset_crossings if c.instance_path == instance_path
        )

    def synchronizer_paths(self) -> frozenset[str]:
        """Set of instance paths in the reset-synchronizer set.

        Renderers use this to mark recognized sync-stage flops with
        a badge so the user can distinguish "vetted sync" from "raw
        reset" — see the Phase 3 issue's "Reset-synchronizer markers"
        bullet.
        """
        return frozenset(s.instance_path for s in self.reset_synchronizers)

    def flop_reset(self, instance_path: str) -> FlopReset | None:
        """Lookup the reset binding for a specific flop, or ``None``.

        Linear scan — the artefact is small (one entry per flop with
        a reset pin) and renderers only call this per visible node.
        If profiling ever surfaces this as hot, swap to a dict in
        ``__post_init__``.
        """
        for f in self.flop_resets:
            if f.instance_path == instance_path:
                return f
        return None


def load_reset_domain_map(path: Path) -> ResetDomainMap:
    """Load and validate a reset-domain map.

    Raises :class:`ResetAnnotationsError` on missing/malformed
    payloads. File I/O and JSON parse errors are wrapped in the same
    exception so a single ``except`` catches every "annotation map
    didn't load" failure mode — symmetric with
    :func:`rtl_buddy_view.annotations.load_domain_map`.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise ResetAnnotationsError(f"could not read {path}: {e}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ResetAnnotationsError(f"{path}: invalid JSON ({e.msg})") from None
    if not isinstance(payload, dict):
        raise ResetAnnotationsError(f"{path}: top-level must be a JSON object")
    return _parse_payload(payload, source_path=path)


def _parse_payload(payload: dict, *, source_path: Path) -> ResetDomainMap:
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise ResetAnnotationsError(
            f"{source_path}: schema_version missing or not a string"
        )
    major = _major(version)
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise ResetAnnotationsError(
            f"{source_path}: schema_version {version!r} is not supported "
            f"(consumer expects {SUPPORTED_SCHEMA_MAJOR}.x); upgrade "
            f"rtl-buddy-view or downgrade the producing rtl-buddy-cdc."
        )

    generator = _require_dict(payload, "generator", source_path)
    design = _require_dict(payload, "design", source_path)
    return ResetDomainMap(
        schema_version=version,
        generator_name=_require_str(generator, "name", "generator", source_path),
        generator_version=_require_str(generator, "version", "generator", source_path),
        design_top=_require_str(design, "top", "design", source_path),
        design_frontend=_require_str(design, "frontend", "design", source_path),
        reset_sources=tuple(
            _parse_reset_source(s, source_path)
            for s in payload.get("reset_sources", [])
        ),
        reset_synchronizers=tuple(
            _parse_reset_synchronizer(s, source_path)
            for s in payload.get("reset_synchronizers", [])
        ),
        flop_resets=tuple(
            _parse_flop_reset(f, source_path) for f in payload.get("flop_resets", [])
        ),
        reset_crossings=tuple(
            _parse_reset_crossing(c, source_path)
            for c in payload.get("reset_crossings", [])
        ),
    )


# --- per-section parsers -----------------------------------------------------


def _parse_reset_source(entry: dict, source_path: Path) -> ResetSource:
    clock = entry.get("clock")
    if clock is not None and not isinstance(clock, str):
        raise ResetAnnotationsError(
            f"{source_path}: reset_sources[].clock must be string or null"
        )
    declared = entry.get("declared_polarity")
    if declared is not None and not isinstance(declared, str):
        raise ResetAnnotationsError(
            f"{source_path}: reset_sources[].declared_polarity must be string or null"
        )
    return ResetSource(
        name=_require_str(entry, "name", "reset_sources[]", source_path),
        source=_require_str(entry, "source", "reset_sources[]", source_path),  # type: ignore[arg-type]
        polarity=_require_str(entry, "polarity", "reset_sources[]", source_path),  # type: ignore[arg-type]
        type=_require_str(entry, "type", "reset_sources[]", source_path),  # type: ignore[arg-type]
        clock=clock,
        via_synchronizer=bool(entry.get("via_synchronizer", False)),
        declared_polarity=declared,  # type: ignore[arg-type]
        location=_parse_location(entry.get("location")),
    )


def _parse_reset_synchronizer(entry: dict, source_path: Path) -> ResetSynchronizer:
    dest = entry.get("dest_clock")
    if dest is not None and not isinstance(dest, str):
        raise ResetAnnotationsError(
            f"{source_path}: reset_synchronizers[].dest_clock must be string or null"
        )
    async_in = entry.get("async_in")
    if async_in is not None and not isinstance(async_in, str):
        raise ResetAnnotationsError(
            f"{source_path}: reset_synchronizers[].async_in must be string or null"
        )
    async_in_kind = entry.get("async_in_kind")
    if async_in_kind is not None and not isinstance(async_in_kind, str):
        raise ResetAnnotationsError(
            f"{source_path}: reset_synchronizers[].async_in_kind must be string or null"
        )
    return ResetSynchronizer(
        instance_path=_require_str(
            entry, "instance_path", "reset_synchronizers[]", source_path
        ),
        dest_clock=dest,
        async_in=async_in,
        async_in_kind=async_in_kind,  # type: ignore[arg-type]
        location=_parse_location(entry.get("location")),
    )


def _parse_flop_reset(entry: dict, source_path: Path) -> FlopReset:
    clock = entry.get("clock")
    if clock is not None and not isinstance(clock, str):
        raise ResetAnnotationsError(
            f"{source_path}: flop_resets[].clock must be string or null"
        )
    return FlopReset(
        instance_path=_require_str(
            entry, "instance_path", "flop_resets[]", source_path
        ),
        clock=clock,
        reset=_require_str(entry, "reset", "flop_resets[]", source_path),
        reset_kind=_require_str(  # type: ignore[arg-type]
            entry, "reset_kind", "flop_resets[]", source_path
        ),
        polarity=_require_str(entry, "polarity", "flop_resets[]", source_path),  # type: ignore[arg-type]
        type=_require_str(entry, "type", "flop_resets[]", source_path),  # type: ignore[arg-type]
        location=_parse_location(entry.get("location")),
    )


def _parse_reset_crossing(entry: dict, source_path: Path) -> ResetCrossing:
    flop_clock = entry.get("flop_clock")
    if flop_clock is not None and not isinstance(flop_clock, str):
        raise ResetAnnotationsError(
            f"{source_path}: reset_crossings[].flop_clock must be string or null"
        )
    return ResetCrossing(
        instance_path=_require_str(
            entry, "instance_path", "reset_crossings[]", source_path
        ),
        kind=_require_str(entry, "kind", "reset_crossings[]", source_path),  # type: ignore[arg-type]
        flop_clock=flop_clock,
        reset=_require_str(entry, "reset", "reset_crossings[]", source_path),
        reset_kind=_require_str(  # type: ignore[arg-type]
            entry, "reset_kind", "reset_crossings[]", source_path
        ),
        polarity=_require_str(entry, "polarity", "reset_crossings[]", source_path),  # type: ignore[arg-type]
        type=_require_str(entry, "type", "reset_crossings[]", source_path),  # type: ignore[arg-type]
        location=_parse_location(entry.get("location")),
    )


def _parse_location(raw) -> SourceLocation | None:
    if not isinstance(raw, dict) or "file" not in raw:
        return None
    return SourceLocation(
        file=raw["file"],
        start_line=raw.get("start_line"),
        start_column=raw.get("start_column"),
        end_line=raw.get("end_line"),
        end_column=raw.get("end_column"),
    )


# --- typed-dict helpers ------------------------------------------------------


def _require_dict(payload: dict, key: str, source_path: Path) -> dict:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ResetAnnotationsError(
            f"{source_path}: top-level {key!r} must be an object"
        )
    return value


def _require_str(payload: dict, key: str, where: str, source_path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ResetAnnotationsError(f"{source_path}: {where}.{key} must be a string")
    return value


def _major(version: str) -> int:
    """Return the leading major-version integer of ``"<major>.<minor>"``.

    Tolerates a stray ``v`` prefix and trailing patch components; we
    care only about the major.
    """
    cleaned = version.lstrip("v")
    head = cleaned.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        raise ResetAnnotationsError(
            f"schema_version {version!r} doesn't start with a numeric major"
        ) from None
