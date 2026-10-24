"""Clock → colour assignment for the dot / mermaid renderers (#196).

Before #196 every renderer coloured a clock by hashing its name into
seven pastel swatches. The hash is stable across runs, but seven
buckets collide constantly: two main domains of one design (``s_clk``
and ``f_clk``) shared an orange, which defeats a per-domain fill. A
figure now gets its colours from :func:`assign_clock_colors`:

1. **Chosen colours win.** An author can pin a clock's colour with
   the ``clock-colors`` overlay (``--overlay clock-colors=map.json``)
   or a ``// rbsch: clock color=blue`` net pragma on the clock's port.
2. **The rest share out the swatches** in sorted name order, skipping
   swatches a chosen colour already took, so no two clocks in one
   figure collide while there are swatches left.
3. **Only when there are more clocks than free swatches** does the
   name hash come back, as the fallback it always was — collisions
   are unavoidable then, and the hash at least keeps a clock's colour
   independent of which other clocks happen to be present.

Rule 2 is the same sorted-index rule the SPA's
``viewer/src/palette.js::buildClockPalette`` uses, so a figure and the
hub canvas agree on the colours whenever no colour is chosen and the
design has at most seven clocks.

Pure functions apart from :func:`load_clock_colors`, the one I/O entry
point (shaped like :func:`rtl_buddy_view.hints.load_hint_sidecar`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rtl_buddy_view.annotations import DomainMap

if TYPE_CHECKING:  # hints imports parse_color from here
    from rtl_buddy_view.hints import HintMap

#: The swatches, in assignment order. Tuned for legibility on light
#: and dark backgrounds; ``viewer/src/tokens.css`` ``--clk-1`` …
#: ``--clk-7`` carry the same light-theme values in the same order.
CLOCK_PALETTE: tuple[str, ...] = (
    "#dbeafe",  # blue
    "#dcfce7",  # green
    "#fef9c3",  # yellow
    "#fce7f3",  # pink
    "#ede9fe",  # purple
    "#fed7aa",  # orange
    "#cffafe",  # cyan
)

#: The names a chosen colour may use instead of a hex value: one per
#: swatch, so ``color=blue`` is the palette's blue rather than X11's
#: saturated ``blue``, which no pastel fill should be.
SWATCH_NAMES: Mapping[str, str] = {
    "blue": CLOCK_PALETTE[0],
    "green": CLOCK_PALETTE[1],
    "yellow": CLOCK_PALETTE[2],
    "pink": CLOCK_PALETTE[3],
    "purple": CLOCK_PALETTE[4],
    "orange": CLOCK_PALETTE[5],
    "cyan": CLOCK_PALETTE[6],
}

#: Schema version of the ``clock-colors`` overlay document.
SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_MAJOR = 1

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ClockColorsError(ValueError):
    """Raised when a ``clock-colors`` document can't be loaded."""


def parse_color(text: str) -> str | None:
    """Normalise a chosen colour, or ``None`` when it isn't one.

    Accepts a swatch name (case-insensitive) or ``#rrggbb``; returns
    lower-case ``#rrggbb``. Anything else — X11 names, ``#rgb``,
    ``rgb()`` — is refused: dot and mermaid disagree on those, and one
    spelling must mean one colour in every renderer.
    """
    value = text.strip()
    named = SWATCH_NAMES.get(value.lower())
    if named is not None:
        return named
    if _HEX_RE.match(value):
        return value.lower()
    return None


def color_spelling_help() -> str:
    """The accepted spellings, for error and warning messages."""
    return f"a swatch name ({', '.join(SWATCH_NAMES)}) or #rrggbb"


def hash_color(clock_name: str) -> str:
    """The pre-#196 colour: a stable digest of the name into the palette.

    ``hashlib`` rather than ``hash()`` so it holds across processes and
    ``PYTHONHASHSEED`` values.
    """
    digest = hashlib.sha256(clock_name.encode("utf-8")).digest()
    return CLOCK_PALETTE[digest[0] % len(CLOCK_PALETTE)]


def _is_pseudo_clock(name: str) -> bool:
    """``<unconstrained>``-style names anchor no domain, so take no swatch."""
    return name.startswith("<") and name.endswith(">")


def domain_map_clocks(domain_map: DomainMap) -> tuple[str, ...]:
    """Every clock a figure drawn with ``domain_map`` can colour, sorted.

    The union of the declared clocks (the legend's rows), the flop
    domains (node and cluster fills), the port domains (signal-edge
    colours) and both ends of every crossing (two-tone fills and grid
    cells). Taking the union is what keeps the legend, the fills and
    the crossing cells of one figure on one assignment.
    """
    names: set[str] = {c.name for c in domain_map.clocks}
    names.update(f.clock for f in domain_map.flop_domains if f.clock is not None)
    names.update(p.clock for p in domain_map.port_domains)
    for crossing in domain_map.crossings:
        names.update((crossing.src_clock, crossing.dst_clock))
    return tuple(sorted(n for n in names if n and not _is_pseudo_clock(n)))


def assign_clock_colors(
    clocks: Iterable[str], chosen: Mapping[str, str] | None = None
) -> dict[str, str]:
    """One colour per clock for one figure; see the module docstring.

    ``chosen`` maps clock name → normalised colour (:func:`parse_color`)
    and wins outright, including for a clock ``clocks`` doesn't list.
    The remaining clocks are dealt the swatches no chosen colour
    claimed, in sorted order. A chosen colour stays reserved even when
    its clock is absent from this figure, so across a document set
    that colour only ever means that clock. When they outnumber those swatches, every
    one of them falls back to :func:`hash_color`: a partial deal would
    make a clock's colour depend on its alphabetical neighbours.
    """
    chosen = dict(chosen or {})
    auto = sorted({c for c in clocks if c not in chosen})
    taken = set(chosen.values())
    free = [swatch for swatch in CLOCK_PALETTE if swatch not in taken]
    out = dict(chosen)
    if len(auto) <= len(free):
        out.update(zip(auto, free))
    else:
        out.update((name, hash_color(name)) for name in auto)
    return out


@dataclass(frozen=True)
class ClockColorMap:
    """A loaded ``clock-colors`` overlay: clock name → normalised colour."""

    colors: Mapping[str, str] = field(default_factory=dict)


def parse_clock_colors(payload: object, *, source: str) -> ClockColorMap:
    """Validate a decoded ``clock-colors`` document.

    Shape::

        {"schema_version": "1.0",
         "clocks": {"s_clk": "#dbeafe", "f_clk": "green"}}

    Same strictness as the hint sidecar: a document pointed at on the
    command line raises on a wrong type or an unreadable colour rather
    than silently colouring nothing.
    """
    if not isinstance(payload, dict):
        raise ClockColorsError(f"{source}: top-level must be a JSON object")
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise ClockColorsError(f"{source}: schema_version missing or not a string")
    head = version.split(".", 1)[0]
    if not head.isdigit() or int(head) != SUPPORTED_SCHEMA_MAJOR:
        raise ClockColorsError(
            f"{source}: schema_version {version!r} is not supported "
            f"(consumer expects {SUPPORTED_SCHEMA_MAJOR}.x)"
        )
    raw = payload.get("clocks", {})
    if not isinstance(raw, dict):
        raise ClockColorsError(f"{source}: 'clocks' must be a JSON object")
    colors: dict[str, str] = {}
    for name, value in sorted(raw.items()):
        if not isinstance(value, str):
            raise ClockColorsError(
                f"{source}: clocks.{name} must be a string, got {type(value).__name__}"
            )
        color = parse_color(value)
        if color is None:
            raise ClockColorsError(
                f"{source}: clocks.{name}: {value!r} is not a colour; "
                f"use {color_spelling_help()}"
            )
        colors[name] = color
    return ClockColorMap(colors=colors)


def load_clock_colors(path: Path) -> ClockColorMap:
    """Read + validate a ``clock-colors`` document."""
    try:
        text = path.read_text()
    except OSError as e:
        raise ClockColorsError(f"could not read {path}: {e}") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClockColorsError(f"{path}: invalid JSON ({e.msg})") from None
    return parse_clock_colors(payload, source=str(path))


def chosen_clock_colors(
    domain_map: DomainMap | None,
    *,
    hints: HintMap | None = None,
    modules: Iterable[str] = (),
    overlay: ClockColorMap | None = None,
) -> dict[str, str]:
    """The author-chosen colours for one figure, keyed by clock name.

    Two sources, the overlay winning on conflict (it is the deliberate
    command-line artefact, as the hint sidecar is over pragmas):

    * the ``clock-colors`` overlay, keyed by clock name directly;
    * a net hint's ``color`` (``// rbsch: clock color=blue``) on a net
      of one of ``modules`` — the rendered root and the domain map's
      ``design_top``, where a clock's source ports live. It colours
      the clock of the same name, and every clock whose ``ports`` list
      names that net. A hint in ``modules[0]`` beats one in a later
      module.
    """
    out: dict[str, str] = {}
    if hints is not None and domain_map is not None:
        known = set(domain_map_clocks(domain_map))
        for module in reversed(list(dict.fromkeys(modules))):
            for net, hint in sorted(hints.nets_for_module(module).items()):
                if not hint.color:
                    continue
                if net in known:
                    out[net] = hint.color
                for clock in domain_map.clocks:
                    if net in clock.ports:
                        out[clock.name] = hint.color
    if overlay is not None:
        out.update(overlay.colors)
    return out
