"""TB-context clock + reset map (rtl-buddy-view #99 / phase 6e).

The DUT-side :class:`DomainMap` (rtl-buddy-cdc#106) is derived from
SDC: ``create_clock`` / ``set_clock_groups`` etc. live in the
design's timing contract. The testbench has no SDC — TB clocks come
from procedural ``initial`` / ``always #5`` blocks, resets from
``initial begin rst=1; #20 rst=0; end``. Phase 6d ships the
default-unannotated path (DUT subtree keeps clock tints, TB scopes
above stay grey). Phase 6e adds an opt-in upgrade: a hand-authored
``tb_clock_map.json`` next to ``tests.yaml`` that names each TB
clock + the instance paths it drives.

Loader + dataclasses live here; the renderer-time merge with the
DUT-side map lives in :func:`merge_into_domain_map`. DUT-side wins
inside every instance whose module matches the DUT top (matches the
overlay-anchoring contract called out by #99); TB-side fills the
gap outside. Boundary mismatches (a DUT pin named on both sides
with different clocks) surface as a stderr warning at load time —
not a fatal error — and DUT-side wins inside the boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable

from rtl_buddy_view.annotations import (
    AnnotationsError,
    Clock,
    DomainMap,
    FlopDomain,
)
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.reset_annotations import (
    FlopReset,
    ResetDomainMap,
    ResetPolarity,
)


SUPPORTED_SCHEMA_MAJOR = 1
"""Schema major this loader understands. Mismatch raises
:class:`AnnotationsError` — same contract as the DUT-side
:func:`annotations.load_domain_map`."""


@dataclass(frozen=True)
class TbClock:
    """One TB-level clock entry.

    ``drives`` is a list of TB-absolute instance paths (paths rooted
    at the testbench top) that this clock tints. The renderer
    expands each entry into a synthetic :class:`FlopDomain` at the
    DUT-side merge step so existing per-node clock contribution
    code (``predominant_clock`` etc.) works unchanged.
    """

    name: str
    drives: tuple[str, ...]
    period_ns: float | None = None


@dataclass(frozen=True)
class TbReset:
    """One TB-level reset entry.

    Mirrors :class:`TbClock`. ``active_low`` replaces what
    ``set_input_delay``/polarity carries on the DUT side.
    """

    name: str
    drives: tuple[str, ...]
    active_low: bool = False


@dataclass(frozen=True)
class TbClockMap:
    """Parsed ``tb_clock_map.json``.

    Schema is intentionally minimal — most TBs have ≤3 clocks and
    ≤2 resets — and self-describes via ``rtl-buddy-filetype:
    tb_clock_map`` so the loader can reject a domain_map.json fed in
    by mistake.
    """

    schema_version: str
    clocks: tuple[TbClock, ...] = ()
    resets: tuple[TbReset, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.clocks and not self.resets


def load_tb_clock_map(path: Path) -> TbClockMap:
    """Load + validate a TB clock/reset map.

    Same exception shape as :func:`annotations.load_domain_map` so a
    single ``except AnnotationsError`` catches every "annotation
    didn't load" failure mode from the caller's perspective.
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


def _parse_payload(payload: dict, *, source_path: Path) -> TbClockMap:
    filetype = payload.get("rtl-buddy-filetype")
    if filetype != "tb_clock_map":
        raise AnnotationsError(
            f"{source_path}: rtl-buddy-filetype must be 'tb_clock_map' "
            f"(got {filetype!r}); did you pass a domain_map.json by mistake?"
        )
    version = payload.get("schema_version")
    if not isinstance(version, str) or not version:
        raise AnnotationsError(f"{source_path}: schema_version missing or not a string")
    try:
        major = int(version.split(".", 1)[0])
    except ValueError as e:
        raise AnnotationsError(
            f"{source_path}: schema_version {version!r} does not start "
            f"with a numeric major"
        ) from e
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise AnnotationsError(
            f"{source_path}: schema major {major} not supported "
            f"(expected {SUPPORTED_SCHEMA_MAJOR}.x)"
        )

    clocks = tuple(
        _parse_clock(c, source_path=source_path, index=i)
        for i, c in enumerate(payload.get("clocks", []) or [])
    )
    resets = tuple(
        _parse_reset(r, source_path=source_path, index=i)
        for i, r in enumerate(payload.get("resets", []) or [])
    )
    return TbClockMap(schema_version=version, clocks=clocks, resets=resets)


def _parse_clock(entry: object, *, source_path: Path, index: int) -> TbClock:
    if not isinstance(entry, dict):
        raise AnnotationsError(f"{source_path}: clocks[{index}] must be an object")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise AnnotationsError(
            f"{source_path}: clocks[{index}].name must be a non-empty string"
        )
    drives = _parse_drives(
        entry.get("drives"), source_path=source_path, what=f"clocks[{index}].drives"
    )
    period_raw = entry.get("period_ns")
    if period_raw is not None and not isinstance(period_raw, (int, float)):
        raise AnnotationsError(
            f"{source_path}: clocks[{index}].period_ns must be a number or null"
        )
    return TbClock(
        name=name,
        drives=drives,
        period_ns=float(period_raw) if period_raw is not None else None,
    )


def _parse_reset(entry: object, *, source_path: Path, index: int) -> TbReset:
    if not isinstance(entry, dict):
        raise AnnotationsError(f"{source_path}: resets[{index}] must be an object")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise AnnotationsError(
            f"{source_path}: resets[{index}].name must be a non-empty string"
        )
    drives = _parse_drives(
        entry.get("drives"), source_path=source_path, what=f"resets[{index}].drives"
    )
    active_low_raw = entry.get("active_low", False)
    if not isinstance(active_low_raw, bool):
        raise AnnotationsError(
            f"{source_path}: resets[{index}].active_low must be a boolean"
        )
    return TbReset(name=name, drives=drives, active_low=active_low_raw)


def _parse_drives(raw: object, *, source_path: Path, what: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise AnnotationsError(
            f"{source_path}: {what} must be a non-empty list of instance-path strings"
        )
    out: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            raise AnnotationsError(
                f"{source_path}: {what}[{i}] must be a non-empty string"
            )
        out.append(item)
    return tuple(out)


# ---------------------------------------------------------------------------
# Merge into the DUT-side maps
# ---------------------------------------------------------------------------


def merge_into_domain_map(
    dut_map: DomainMap | None,
    tb_map: TbClockMap,
    *,
    root: HierNode,
    dut_top_module: str | None,
    warn_stream: IO[str] | None = None,
) -> DomainMap:
    """Return a new ``DomainMap`` that unions ``tb_map`` into ``dut_map``.

    Strategy: synthesize a :class:`FlopDomain` for every TB-side
    drive that is *outside* every DUT subtree. The renderer's
    existing per-node clock contribution then tints those nodes
    without any further integration. Within a DUT subtree the DUT-
    side map already covers the inner flops; if a TB-side drive
    aliases a DUT-side path, emit a load-time warning and let the
    DUT-side win (the rule the issue pinned).

    ``dut_top_module`` identifies which subtrees are "the DUT" —
    matches every :class:`HierNode` whose ``module_name == dut_top_module``.
    When ``None`` (no ``--top`` supplied alongside ``--tb-top``), no
    DUT subtree exists and every TB-side drive is treated as
    outside-the-DUT.

    ``dut_map`` may be ``None`` — covers projects that ship only a
    TB-side map, with no DUT-side CDC analysis available. The
    synthesized result still tints the TB scope.
    """
    # 1. Enumerate DUT-instance subtrees (instance paths whose module
    #    matches dut_top_module). Boundary detection is the prefix
    #    rule: a TB-drive path is "inside" the DUT iff it equals one
    #    of these anchors or starts with ``<anchor>.``.
    dut_anchors: list[str] = []
    if dut_top_module:
        for node in _walk(root):
            if node.module_name == dut_top_module:
                dut_anchors.append(node.instance_path)

    # 2. Walk TB clocks; emit synthetic FlopDomain entries for every
    #    drive path outside the DUT subtrees. Warn on boundary aliases.
    new_flops: list[FlopDomain] = []
    new_clocks: list[Clock] = []
    seen_clock_names: set[str] = (
        {c.name for c in dut_map.clocks} if dut_map is not None else set()
    )
    for clock in tb_map.clocks:
        if clock.name not in seen_clock_names:
            new_clocks.append(
                Clock(
                    name=clock.name,
                    period=clock.period_ns,
                    source="tb_clock_map",
                    ports=(),
                )
            )
            seen_clock_names.add(clock.name)
        for drive in clock.drives:
            if _is_inside_any(drive, dut_anchors):
                _warn(
                    warn_stream,
                    f"tb_clock_map: drive {drive!r} is inside a DUT instance "
                    f"({_enclosing_anchor(drive, dut_anchors)!r}); "
                    f"DUT-side domain map wins for clock {clock.name!r}",
                )
                continue
            new_flops.append(
                FlopDomain(
                    instance_path=drive,
                    clock=clock.name,
                    location=None,
                    source_instance_path=drive,
                )
            )

    # 3. Build the merged DomainMap. When no DUT-side map was
    #    supplied, fabricate a minimal envelope so downstream
    #    overlays_present() etc. behave as if the map exists.
    if dut_map is None:
        return DomainMap(
            schema_version="1.0",
            generator_name="rtl-buddy-view",
            generator_version="tb_clock_map merge",
            design_top=root.module_name,
            design_frontend="merged",
            clocks=tuple(new_clocks),
            flop_domains=tuple(new_flops),
        )
    return DomainMap(
        schema_version=dut_map.schema_version,
        generator_name=dut_map.generator_name,
        generator_version=dut_map.generator_version,
        design_top=dut_map.design_top,
        design_frontend=dut_map.design_frontend,
        clocks=dut_map.clocks + tuple(new_clocks),
        generated_clocks=dut_map.generated_clocks,
        clock_groups=dut_map.clock_groups,
        false_path_pairs=dut_map.false_path_pairs,
        flop_domains=dut_map.flop_domains + tuple(new_flops),
        port_domains=dut_map.port_domains,
        crossings=dut_map.crossings,
    )


def merge_into_reset_map(
    dut_map: ResetDomainMap | None,
    tb_map: TbClockMap,
    *,
    root: HierNode,
    dut_top_module: str | None,
    warn_stream: IO[str] | None = None,
) -> ResetDomainMap:
    """Reset-side mirror of :func:`merge_into_domain_map`.

    The DUT-side :class:`ResetDomainMap` already carries
    :class:`FlopReset` entries. We synthesize parallel entries for
    each TB-reset drive outside any DUT subtree. The clock-context
    on TB-side flops is unknown (no SDC), so :class:`FlopReset`'s
    ``clock`` field is left as ``None`` — consumers that gate on
    matching ``flop.clock`` to a real clock should treat this as
    "TB-side, no clock context".
    """
    dut_anchors: list[str] = []
    if dut_top_module:
        for node in _walk(root):
            if node.module_name == dut_top_module:
                dut_anchors.append(node.instance_path)

    new_resets: list[FlopReset] = []
    for reset in tb_map.resets:
        polarity: ResetPolarity = "low" if reset.active_low else "high"
        for drive in reset.drives:
            if _is_inside_any(drive, dut_anchors):
                _warn(
                    warn_stream,
                    f"tb_clock_map: reset drive {drive!r} is inside a "
                    f"DUT instance ({_enclosing_anchor(drive, dut_anchors)!r}); "
                    f"DUT-side reset map wins for reset {reset.name!r}",
                )
                continue
            new_resets.append(
                FlopReset(
                    instance_path=drive,
                    clock=None,
                    reset=reset.name,
                    reset_kind="port",
                    polarity=polarity,
                    type="async",
                    location=None,
                )
            )

    if dut_map is None:
        return ResetDomainMap(
            schema_version="1.0",
            generator_name="rtl-buddy-view",
            generator_version="tb_clock_map merge",
            design_top=root.module_name,
            design_frontend="merged",
            flop_resets=tuple(new_resets),
        )
    return ResetDomainMap(
        schema_version=dut_map.schema_version,
        generator_name=dut_map.generator_name,
        generator_version=dut_map.generator_version,
        design_top=dut_map.design_top,
        design_frontend=dut_map.design_frontend,
        flop_resets=dut_map.flop_resets + tuple(new_resets),
        reset_synchronizers=dut_map.reset_synchronizers,
        reset_crossings=dut_map.reset_crossings,
        reset_sources=dut_map.reset_sources,
    )


def _walk(node: HierNode) -> Iterable[HierNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _is_inside_any(path: str, anchors: list[str]) -> bool:
    for a in anchors:
        if path == a or path.startswith(a + "."):
            return True
    return False


def _enclosing_anchor(path: str, anchors: list[str]) -> str:
    for a in anchors:
        if path == a or path.startswith(a + "."):
            return a
    return ""


def _warn(stream: IO[str] | None, message: str) -> None:
    if stream is None:
        return
    stream.write(f"warning: {message}\n")
