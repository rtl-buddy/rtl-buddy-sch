"""Plugin overlay layer (Phase 4 — #17).

The pre-Phase-4 codebase carries two domain-specific annotation
flags (``--cdc-annotations``, ``--rdc-annotations``) plus matching
per-loader plumbing inside each renderer. That works for two
overlays; it doesn't scale to five (coverage, physical, waveform
all queued behind this).

This module replaces that pattern with a single generalized
plugin layer:

- :class:`Overlay` — protocol every overlay implements (loader +
  joiner + render contribution).
- :class:`Annotation` — opaque marker base for the per-overlay
  parsed data so the registry can pass overlays around without
  caring about their concrete payload shape.
- :class:`OverlayRegistry` — name → Overlay instance dispatch.
  Built-ins (``clock``, ``reset``) are registered at import time
  by :func:`default_registry`. Third-party packages register via
  the :data:`ENTRY_POINT_GROUP` (``rtl_buddy_view.overlays``)
  entry-point group; :func:`default_registry` discovers them
  after the built-ins so name collisions deterministically resolve
  in favour of the in-tree overlay (and emit a stderr warning so
  the user can spot the shadowing).

The registry deliberately doesn't *load* anything on its own; the
CLI iterates ``--overlay name=path`` invocations and asks the
registry for the named overlay, then asks the overlay to load its
path. Keeping I/O out of the registry keeps unit tests fast and
makes the failure mode for "unknown overlay name" trivially
inspectable.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import IO, Iterator, Protocol, runtime_checkable

from rtl_buddy_view.graph import HierNode

#: setuptools entry-point group third-party packages register against.
#: Each entry's value should resolve to a no-arg callable (typically
#: the overlay class itself) that returns an :class:`Overlay` instance.
ENTRY_POINT_GROUP = "rtl_buddy_view.overlays"

#: Source label applied to every overlay registered by
#: :func:`default_registry` from this package's own modules. External
#: overlays carry their distribution name (or ``"<unknown-dist>"`` if
#: the entry-point has no associated distribution) instead. Surfaces
#: in ``--list-overlays`` so users can tell built-ins from plugins
#: and identify which package is shadowing a built-in on collision.
BUILTIN_SOURCE = "built-in"

#: Fallback source label for entry points whose distribution lookup
#: fails — should never trigger in practice, but importlib.metadata
#: allows ``EntryPoint.dist`` to be ``None`` so we don't assume.
UNKNOWN_DIST_SOURCE = "<unknown-dist>"


class OverlayError(ValueError):
    """Raised by :meth:`OverlayRegistry.get` and overlay loaders.

    Distinct from the per-overlay loader errors (``AnnotationsError``,
    ``ResetAnnotationsError``) so the CLI can surface "no such overlay
    name" failures with a different message than "this overlay's map
    failed to parse".
    """


@dataclass(frozen=True)
class Annotation:
    """Opaque marker for an overlay's parsed payload.

    Concrete overlays return a subclass-or-equivalent dataclass from
    their :meth:`Overlay.load`; the registry doesn't introspect it —
    it just hands the payload back to the overlay's :meth:`join` and
    :meth:`contribute` methods.

    Holding the overlay name on the wrapper means renderers can ask
    "did the user load this overlay?" without keeping a separate
    presence set.
    """

    name: str
    schema_version: str


@runtime_checkable
class Overlay(Protocol):
    """Contract every overlay implements.

    Implementations are stateless after construction — instances are
    cached in the :class:`OverlayRegistry` and shared across CLI
    invocations within the same process.

    The protocol uses ``runtime_checkable`` so the registry can
    structurally validate third-party overlays at registration
    time (``isinstance(plugin, Overlay)``); concrete overlays don't
    need to inherit from this class.
    """

    name: str
    schema_version: str

    def load(self, path: Path):
        """Parse ``path`` and return an overlay-specific payload.

        Implementations should raise their own loader-specific
        exception subclass on failure; the CLI wraps it for display.
        """
        ...

    def join(self, graph: HierNode, annotation) -> None:
        """Attach overlay metadata onto the hierarchy graph.

        Phase 4 keeps the existing pattern where renderers reach
        into the overlay's payload directly (``flop_reset(path)``,
        ``predominant_clock(path)``) instead of mutating the graph
        — the protocol still includes :meth:`join` so future
        overlays that genuinely need to decorate nodes (coverage
        marking unreached lines, for example) have a hook. The
        clock + reset built-ins implement this as a no-op.
        """
        ...

    def contribute(self, ctx) -> None:
        """Emit this overlay's rendered contribution into ``ctx``.

        The render context is renderer-specific; the overlay
        protocol intentionally leaves the type open so renderers
        can pass whatever scratch state they need. Same rationale
        as :meth:`join` — Phase 4 keeps the existing direct-lookup
        pattern; :meth:`contribute` is the seam for Phase 6+
        overlays that need to participate in rendering more
        actively.
        """
        ...


class OverlayRegistry:
    """Name → :class:`Overlay` dispatch.

    Built-ins live in :func:`default_registry`. Tests construct
    their own registries to exercise specific name sets in
    isolation; the CLI uses the default.

    Each registered overlay carries a ``source`` label
    (:data:`BUILTIN_SOURCE` for in-tree overlays, the distribution
    name for entry-point-discovered ones); :meth:`source_of` exposes
    it so the CLI can annotate ``--list-overlays`` output.
    """

    def __init__(self) -> None:
        self._overlays: dict[str, Overlay] = {}
        self._sources: dict[str, str] = {}

    def register(self, overlay: Overlay, *, source: str = BUILTIN_SOURCE) -> None:
        """Add ``overlay`` to the registry.

        ``source`` labels where the overlay came from so
        :meth:`source_of` (and downstream ``--list-overlays``) can
        distinguish built-ins from third-party plugins. Defaults to
        :data:`BUILTIN_SOURCE` because every existing caller is the
        built-in path; entry-point discovery passes the distribution
        name explicitly.

        Raises :class:`OverlayError` if an overlay with the same
        ``name`` is already registered — silently shadowing would
        make CLI diagnostics confusing ("loaded clock overlay"
        followed by output from a *different* clock overlay).
        """
        if overlay.name in self._overlays:
            existing = self._overlays[overlay.name]
            existing_source = self._sources[overlay.name]
            raise OverlayError(
                f"overlay {overlay.name!r} already registered "
                f"(existing source={existing_source!r}, "
                f"schema_version={existing.schema_version!r}; "
                f"new source={source!r}, "
                f"schema_version={overlay.schema_version!r}); "
                f"third-party overlays must use distinct names"
            )
        self._overlays[overlay.name] = overlay
        self._sources[overlay.name] = source

    def source_of(self, name: str) -> str:
        """Return the source label for the overlay registered under ``name``.

        Raises :class:`OverlayError` on miss for symmetry with
        :meth:`get`; in practice callers iterate the registry and
        look up sources for names they already know are registered.
        """
        try:
            return self._sources[name]
        except KeyError:
            known = sorted(self._overlays)
            raise OverlayError(f"unknown overlay {name!r} (known: {known})") from None

    def get(self, name: str) -> Overlay:
        """Return the overlay registered under ``name``.

        Raises :class:`OverlayError` with the list of known names
        on miss so the CLI can surface "did you mean…" suggestions
        without re-deriving the registry's contents.
        """
        try:
            return self._overlays[name]
        except KeyError:
            known = sorted(self._overlays)
            raise OverlayError(
                f"unknown overlay {name!r} (known: {known}); "
                f"third-party overlays register via the "
                f"'rtl_buddy_view.overlays' entry-point group"
            ) from None

    def names(self) -> tuple[str, ...]:
        """Sorted list of registered overlay names.

        Used by ``--list-overlays`` and by tests that want a stable
        snapshot of "what the CLI offers today."
        """
        return tuple(sorted(self._overlays))

    def __iter__(self) -> Iterator[Overlay]:
        """Iterate in name-sorted order.

        Iteration order matters for ``--list-overlays`` output and
        for the ``overlays_present`` array in ``view.json`` v1 —
        both want deterministic ordering across runs.
        """
        for name in self.names():
            yield self._overlays[name]


def default_registry(*, warn_stream: IO[str] | None = None) -> OverlayRegistry:
    """Build a fresh registry pre-populated with the built-in overlays
    plus any third-party overlays discovered via :data:`ENTRY_POINT_GROUP`.

    The CLI builds one of these per invocation; tests construct
    their own registries to mock subsets in isolation.

    Built-ins live in :mod:`rtl_buddy_view.overlays.clock` and
    :mod:`rtl_buddy_view.overlays.reset`. They're imported lazily
    *inside this function* so importing
    :mod:`rtl_buddy_view.overlays` for the protocol alone (e.g.
    in a third-party package) doesn't drag the built-in loader
    code paths and their dependencies into scope.

    Entry-point discovery loads each entry from the
    ``rtl_buddy_view.overlays`` group; entries that fail to import,
    raise on instantiation, or return something that isn't an
    :class:`Overlay` are *skipped with a stderr warning*. A broken
    third-party overlay must never crash the analyzer for the user
    — they should still be able to render their design and see in
    ``--list-overlays`` that something is wrong with the plugin.

    Name collisions resolve in favour of the built-in: an external
    overlay claiming a built-in name is skipped with a warning.
    Two externals colliding is also surfaced as a warning rather
    than a hard error for the same don't-break-the-user reason.

    ``warn_stream`` defaults to :data:`sys.stderr`; tests inject a
    ``StringIO`` to assert on the messages.
    """
    stream: IO[str] = sys.stderr if warn_stream is None else warn_stream

    from rtl_buddy_view.overlays.axi_perf import AxiPerfOverlay
    from rtl_buddy_view.overlays.clock import ClockOverlay
    from rtl_buddy_view.overlays.clock_tb import ClockTbOverlay
    from rtl_buddy_view.overlays.coverage import CoverageOverlay
    from rtl_buddy_view.overlays.hints import HintsOverlay
    from rtl_buddy_view.overlays.reset import ResetOverlay
    from rtl_buddy_view.overlays.wave import WaveOverlay

    registry = OverlayRegistry()
    registry.register(ClockOverlay())
    registry.register(ResetOverlay())
    registry.register(AxiPerfOverlay())
    registry.register(WaveOverlay())
    registry.register(ClockTbOverlay())
    registry.register(CoverageOverlay())
    registry.register(HintsOverlay())

    for instance, source in _discover_external_overlays(stream):
        if instance.name in registry.names():
            existing_source = registry.source_of(instance.name)
            stream.write(
                f"warning: overlay {instance.name!r} from {source!r} "
                f"is shadowed by the existing {existing_source!r} overlay "
                f"and was skipped; rename the third-party overlay or remove "
                f"the colliding package.\n"
            )
            continue
        registry.register(instance, source=source)

    return registry


def _discover_external_overlays(
    warn_stream: IO[str],
) -> Iterator[tuple[Overlay, str]]:
    """Yield ``(overlay_instance, distribution_name)`` for every
    importable entry point in :data:`ENTRY_POINT_GROUP`.

    Skips entries whose import, instantiation, or protocol check
    fails, with a stderr warning so the user can see which plugin
    is broken. The yield order matches the entry-points iteration
    order; the caller is responsible for deterministic registry
    ordering (the registry itself sorts by name on iteration).
    """
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # pragma: no cover - importlib.metadata edge case
        warn_stream.write(
            f"warning: failed to enumerate {ENTRY_POINT_GROUP!r} entry points: {exc}\n"
        )
        return

    for ep in eps:
        source = _entry_point_dist_name(ep)
        try:
            factory = ep.load()
        except Exception as exc:
            warn_stream.write(
                f"warning: skipping overlay entry point {ep.name!r} "
                f"from {source!r}: failed to import "
                f"({type(exc).__name__}: {exc})\n"
            )
            continue
        try:
            instance = factory() if callable(factory) else factory
        except Exception as exc:
            warn_stream.write(
                f"warning: skipping overlay entry point {ep.name!r} "
                f"from {source!r}: failed to instantiate "
                f"({type(exc).__name__}: {exc})\n"
            )
            continue
        if not isinstance(instance, Overlay):
            warn_stream.write(
                f"warning: skipping overlay entry point {ep.name!r} "
                f"from {source!r}: returned "
                f"{type(instance).__name__}, not an Overlay\n"
            )
            continue
        yield instance, source


def _entry_point_dist_name(ep: EntryPoint) -> str:
    """Best-effort distribution name for ``ep``.

    ``EntryPoint.dist`` was added in Python 3.10; even there it can
    be ``None`` when the entry point came from a non-installed
    source (e.g. constructed in tests). Fall back to a marker label
    rather than raising — diagnostic source attribution shouldn't
    break overlay loading.
    """
    dist = getattr(ep, "dist", None)
    if dist is None:
        return UNKNOWN_DIST_SOURCE
    name = getattr(dist, "name", None)
    return name if name else UNKNOWN_DIST_SOURCE
