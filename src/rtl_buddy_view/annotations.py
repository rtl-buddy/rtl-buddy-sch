"""Loader for the rtl-buddy-cdc clock-domain map (Phase 2 overlay input).

Consumes the JSON artifact produced by ``rtl-buddy-cdc lint
--emit-domain-map`` (tracked at
[rtl-buddy/rtl-buddy-cdc#106](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/106)).
Schema is owned by rtl-buddy-cdc; this module is the consumer side
and validates the version field on load so an incompatible producer
fails loudly instead of silently misrendering.

Phase 2 scope:

- Load + structurally validate the v1.0 schema.
- Surface the parsed model to renderers (handled in the next PR).
- Graceful no-SDC degradation: when ``clocks == []``, the map still
  parses cleanly and downstream renderers fall back to the
  un-annotated Phase 1 behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rtl_buddy_view.extractor import SourceLocation

#: Highest schema major.minor we know how to consume. Bumping to a
#: new major version is a breaking change; minor bumps must remain
#: backward-compatible from the producer side per the schema
#: contract pinned in rtl-buddy-cdc#106. We accept any 1.x payload
#: today; producers MAY add fields freely as long as they don't
#: change the existing types/keys.
SUPPORTED_SCHEMA_MAJOR = 1


class AnnotationsError(ValueError):
    """Raised when a domain-map payload can't be loaded.

    Distinct from generic JSON / IO errors so callers can surface a
    targeted "annotation map is malformed" message rather than a
    confusing stack trace.
    """


@dataclass(frozen=True)
class Clock:
    name: str
    period: float | None
    source: str
    ports: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedClock:
    name: str
    master: str
    period: float | None
    divide_by: int
    multiply_by: int


@dataclass(frozen=True)
class ClockGroup:
    """One ``set_clock_groups`` invocation.

    ``members`` is ``list[list[str]]`` — the outer list is per
    ``-group`` clause, the inner list is the clocks named in that
    group. Clocks in different inner lists are async-vs-each-other
    (or exclusive-vs-each-other, depending on ``kind``).
    """

    kind: str  # "asynchronous" | "exclusive"
    members: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class FlopDomain:
    instance_path: str
    clock: str | None  # None = untraceable / no SDC
    location: SourceLocation | None
    # rtl-buddy-cdc#136 (schema v1.x additive): the deepest enclosing
    # source-level instance path for this flop. ``None`` for old
    # producers that didn't emit the field; consumers should fall
    # back to ``instance_path`` (or string-prefix walking) in that
    # case. Renderers prefer this when present because the synth
    # backend's ``instance_path`` is a netlist-flop name that never
    # matches a source-instance path exactly.
    source_instance_path: str | None = None


@dataclass(frozen=True)
class PortDomain:
    module: str
    port: str
    clock: str
    kind: str  # "input" | "output"


@dataclass(frozen=True)
class Crossing:
    """A register-to-register or port-to-register crossing.

    Exactly one of ``src_flop`` / ``src_port`` is set; the other is
    None. ``async_per_sdc`` distinguishes a true CDC (async source +
    dest) from a same-domain crossing that the analyzer still
    surfaces for completeness.
    """

    src_clock: str
    dst_clock: str
    dst_flop: str
    min_hops: int
    width: int
    async_per_sdc: bool
    src_flop: str | None = None
    src_port: str | None = None
    # rtl-buddy-cdc#136 fields: resolved source-instance paths for the
    # destination and (optional) source flops. See ``FlopDomain`` for
    # the same rationale.
    dst_source_instance_path: str | None = None
    src_source_instance_path: str | None = None


@dataclass(frozen=True)
class DomainMap:
    schema_version: str
    generator_name: str
    generator_version: str
    design_top: str
    design_frontend: str
    clocks: tuple[Clock, ...] = ()
    generated_clocks: tuple[GeneratedClock, ...] = ()
    clock_groups: tuple[ClockGroup, ...] = ()
    false_path_pairs: tuple[tuple[str, str], ...] = ()
    flop_domains: tuple[FlopDomain, ...] = ()
    port_domains: tuple[PortDomain, ...] = ()
    crossings: tuple[Crossing, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when no SDC was supplied to the producer.

        Phase 2 renderers fall back to un-annotated rendering when
        the map is "empty" in this sense rather than half-coloring
        the diagram with no actual clock information.
        """
        return not self.clocks

    def crossings_into(
        self, instance_path: str, *, async_only: bool = True
    ) -> tuple["Crossing", ...]:
        """Crossings whose destination is the source instance ``instance_path``.

        When the producer emitted ``dst_source_instance_path``
        (rtl-buddy-cdc#136), match against that — the field maps the
        synth-internal ``dst_flop`` to the deepest enclosing source
        instance, so renderers don't need to know about synth naming
        conventions. Falls back to exact ``dst_flop`` match for
        producers that predate the field.

        Default filters to ``async_per_sdc=True`` — the SDC-confirmed
        true-CDC subset. Pass ``async_only=False`` to get every
        crossing the analyzer found regardless of SDC verdict.
        """
        return tuple(
            c
            for c in self.crossings
            if _crossing_targets(c, instance_path)
            and (not async_only or c.async_per_sdc)
        )

    def predominant_clock(self, instance_path: str) -> str | None:
        """Return the most common clock among flops under ``instance_path``.

        "Under" is computed against ``source_instance_path`` when the
        producer emitted it (rtl-buddy-cdc#136) — that's the field that
        maps a synth-internal flop name to the source instance it lives
        in. For older producers, falls back to the netlist
        ``instance_path``, which prefix-matched correctly for
        slang-style ``…u_sync.$slang$sdff$N`` names but silently misses
        on flattened synthesis.

        Flops with ``clock=None`` (untraceable) are ignored. Ties
        between clocks with the same flop count are broken
        alphabetically so the result is stable across runs.
        """
        prefix = instance_path + "."
        counts: dict[str, int] = {}
        for flop in self.flop_domains:
            if flop.clock is None:
                continue
            owner = flop.source_instance_path or flop.instance_path
            if owner == instance_path or owner.startswith(prefix):
                counts[flop.clock] = counts.get(flop.clock, 0) + 1
        if not counts:
            return None
        # Sort by count descending, then name ascending. Sorting (not
        # max) keeps the tie-break logic obvious — the (-count, name)
        # key makes Python's default ascending sort do both jobs.
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return ordered[0][0]


def _crossing_targets(c: "Crossing", instance_path: str) -> bool:
    """``True`` when ``c`` terminates at ``instance_path`` in source space.

    Prefers ``dst_source_instance_path`` when set (rtl-buddy-cdc#136),
    else falls back to exact match on ``dst_flop``.
    """
    if c.dst_source_instance_path is not None:
        return c.dst_source_instance_path == instance_path
    return c.dst_flop == instance_path


def load_domain_map(path: Path) -> DomainMap:
    """Load and validate a clock-domain map.

    Raises :class:`AnnotationsError` on missing/malformed payloads.
    File I/O and JSON parse errors are wrapped in the same exception
    so a single ``except`` catches every "annotation map didn't
    load" failure mode.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise AnnotationsError(f"could not read {path}: {e}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise AnnotationsError(f"{path}: invalid JSON ({e.msg})") from None
    if not isinstance(payload, dict):
        raise AnnotationsError(f"{path}: top-level must be a JSON object")
    return _parse_payload(payload, source_path=path)


def _parse_payload(payload: dict, *, source_path: Path) -> DomainMap:
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise AnnotationsError(f"{source_path}: schema_version missing or not a string")
    major = _major(version)
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise AnnotationsError(
            f"{source_path}: schema_version {version!r} is not supported "
            f"(consumer expects {SUPPORTED_SCHEMA_MAJOR}.x); upgrade "
            f"rtl-buddy-view or downgrade the producing rtl-buddy-cdc."
        )

    generator = _require_dict(payload, "generator", source_path)
    design = _require_dict(payload, "design", source_path)
    return DomainMap(
        schema_version=version,
        generator_name=_require_str(generator, "name", "generator", source_path),
        generator_version=_require_str(generator, "version", "generator", source_path),
        design_top=_require_str(design, "top", "design", source_path),
        design_frontend=_require_str(design, "frontend", "design", source_path),
        clocks=tuple(_parse_clock(c, source_path) for c in payload.get("clocks", [])),
        generated_clocks=tuple(
            _parse_generated_clock(g, source_path)
            for g in payload.get("generated_clocks", [])
        ),
        clock_groups=tuple(
            _parse_clock_group(g, source_path) for g in payload.get("clock_groups", [])
        ),
        false_path_pairs=tuple(
            _parse_pair(p, source_path) for p in payload.get("false_path_pairs", [])
        ),
        flop_domains=tuple(
            _parse_flop_domain(f, source_path) for f in payload.get("flop_domains", [])
        ),
        port_domains=tuple(
            _parse_port_domain(p, source_path) for p in payload.get("port_domains", [])
        ),
        crossings=tuple(
            _parse_crossing(c, source_path) for c in payload.get("crossings", [])
        ),
    )


# --- per-section parsers -----------------------------------------------------


def _parse_clock(entry: dict, source_path: Path) -> Clock:
    return Clock(
        name=_require_str(entry, "name", "clocks[]", source_path),
        period=_optional_float(entry.get("period")),
        source=_require_str(entry, "source", "clocks[]", source_path),
        ports=tuple(entry.get("ports", [])),
    )


def _parse_generated_clock(entry: dict, source_path: Path) -> GeneratedClock:
    return GeneratedClock(
        name=_require_str(entry, "name", "generated_clocks[]", source_path),
        master=_require_str(entry, "master", "generated_clocks[]", source_path),
        period=_optional_float(entry.get("period")),
        divide_by=int(entry.get("divide_by", 1)),
        multiply_by=int(entry.get("multiply_by", 1)),
    )


def _parse_clock_group(entry: dict, source_path: Path) -> ClockGroup:
    kind = _require_str(entry, "kind", "clock_groups[]", source_path)
    raw_members = entry.get("members", [])
    if not isinstance(raw_members, list):
        raise AnnotationsError(f"{source_path}: clock_groups[].members must be a list")
    return ClockGroup(
        kind=kind,
        members=tuple(tuple(group) for group in raw_members),
    )


def _parse_pair(entry, source_path: Path) -> tuple[str, str]:
    if not isinstance(entry, list) or len(entry) != 2:
        raise AnnotationsError(
            f"{source_path}: false_path_pairs entry must be a 2-element list"
        )
    a, b = entry
    if not (isinstance(a, str) and isinstance(b, str)):
        raise AnnotationsError(
            f"{source_path}: false_path_pairs entries must be string pairs"
        )
    return (a, b)


def _parse_flop_domain(entry: dict, source_path: Path) -> FlopDomain:
    path = _require_str(entry, "instance_path", "flop_domains[]", source_path)
    clock = entry.get("clock")
    if clock is not None and not isinstance(clock, str):
        raise AnnotationsError(
            f"{source_path}: flop_domains[].clock must be string or null"
        )
    sip = entry.get("source_instance_path")
    if sip is not None and not isinstance(sip, str):
        raise AnnotationsError(
            f"{source_path}: flop_domains[].source_instance_path must be string or null"
        )
    return FlopDomain(
        instance_path=path,
        clock=clock,
        location=_parse_location(entry.get("location")),
        source_instance_path=sip,
    )


def _parse_port_domain(entry: dict, source_path: Path) -> PortDomain:
    return PortDomain(
        module=_require_str(entry, "module", "port_domains[]", source_path),
        port=_require_str(entry, "port", "port_domains[]", source_path),
        clock=_require_str(entry, "clock", "port_domains[]", source_path),
        kind=_require_str(entry, "kind", "port_domains[]", source_path),
    )


def _parse_crossing(entry: dict, source_path: Path) -> Crossing:
    dsip = entry.get("dst_source_instance_path")
    if dsip is not None and not isinstance(dsip, str):
        raise AnnotationsError(
            f"{source_path}: crossings[].dst_source_instance_path must be string or null"
        )
    ssip = entry.get("src_source_instance_path")
    if ssip is not None and not isinstance(ssip, str):
        raise AnnotationsError(
            f"{source_path}: crossings[].src_source_instance_path must be string or null"
        )
    return Crossing(
        src_clock=_require_str(entry, "src_clock", "crossings[]", source_path),
        dst_clock=_require_str(entry, "dst_clock", "crossings[]", source_path),
        dst_flop=_require_str(entry, "dst_flop", "crossings[]", source_path),
        min_hops=int(entry.get("min_hops", 0)),
        width=int(entry.get("width", 1)),
        async_per_sdc=bool(entry.get("async_per_sdc", False)),
        src_flop=entry.get("src_flop"),
        src_port=entry.get("src_port"),
        dst_source_instance_path=dsip,
        src_source_instance_path=ssip,
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
        raise AnnotationsError(f"{source_path}: top-level {key!r} must be an object")
    return value


def _require_str(payload: dict, key: str, where: str, source_path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise AnnotationsError(f"{source_path}: {where}.{key} must be a string")
    return value


def _optional_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


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
        raise AnnotationsError(
            f"schema_version {version!r} doesn't start with a numeric major"
        ) from None
