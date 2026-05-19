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
  the ``rtl_buddy_view.overlays`` entry-point group (wired in a
  later subtask of #17 — registry stays import-time pure today).

The registry deliberately doesn't *load* anything on its own; the
CLI iterates ``--overlay name=path`` invocations and asks the
registry for the named overlay, then asks the overlay to load its
path. Keeping I/O out of the registry keeps unit tests fast and
makes the failure mode for "unknown overlay name" trivially
inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from rtl_buddy_view.graph import HierNode


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
    """

    def __init__(self) -> None:
        self._overlays: dict[str, Overlay] = {}

    def register(self, overlay: Overlay) -> None:
        """Add ``overlay`` to the registry.

        Raises :class:`OverlayError` if an overlay with the same
        ``name`` is already registered — silently shadowing would
        make CLI diagnostics confusing ("loaded clock overlay"
        followed by output from a *different* clock overlay).
        """
        if overlay.name in self._overlays:
            existing = self._overlays[overlay.name]
            raise OverlayError(
                f"overlay {overlay.name!r} already registered "
                f"(existing schema_version={existing.schema_version!r}, "
                f"new schema_version={overlay.schema_version!r}); "
                f"third-party overlays must use distinct names"
            )
        self._overlays[overlay.name] = overlay

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


def default_registry() -> OverlayRegistry:
    """Build a fresh registry pre-populated with the built-in overlays.

    The CLI builds one of these per invocation; tests construct
    their own registries to mock subsets in isolation.

    Built-ins live in :mod:`rtl_buddy_view.overlays.clock` and
    :mod:`rtl_buddy_view.overlays.reset`. They're imported lazily
    *inside this function* so importing
    :mod:`rtl_buddy_view.overlays` for the protocol alone (e.g.
    in a third-party package) doesn't drag the built-in loader
    code paths and their dependencies into scope.
    """
    from rtl_buddy_view.overlays.clock import ClockOverlay
    from rtl_buddy_view.overlays.reset import ResetOverlay

    registry = OverlayRegistry()
    registry.register(ClockOverlay())
    registry.register(ResetOverlay())
    return registry
