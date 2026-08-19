"""Tests for third-party overlay discovery via entry points (#46).

Phase 4 (#17) anticipated the ``rtl_buddy_view.overlays`` entry-point
group but kept the registry import-time pure. This phase wires
:func:`rtl_buddy_view.overlays.default_registry` to discover external
overlays, with three properties pinned here:

1. External overlays surface in the registry alongside built-ins,
   carrying their distribution name as the source label.
2. Name collisions with a built-in are resolved in favour of the
   built-in and emit a stderr warning identifying the shadowing
   package.
3. Plugins that fail to import, raise on instantiation, or return
   a non-:class:`Overlay` are skipped with a warning rather than
   crashing the analyzer.

We don't pip-install a sample package — that would couple every
test run to the build backend and slow the suite. Instead we
monkeypatch :func:`importlib.metadata.entry_points` with duck-typed
``EntryPoint`` objects that load locally defined factories.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from rtl_buddy_view.overlays import (
    BUILTIN_SOURCE,
    ENTRY_POINT_GROUP,
    UNKNOWN_DIST_SOURCE,
    Overlay,
    default_registry,
)


# --- fakes ------------------------------------------------------------------


@dataclass
class _FakeDist:
    """Minimal duck-typed stand-in for :class:`importlib.metadata.Distribution`.

    The discovery code only reads ``.name`` off the ``dist`` attribute;
    a full Distribution mock would drag in metadata parsing for zero
    test value.
    """

    name: str


@dataclass
class FakeEntryPoint:
    """Duck-typed :class:`importlib.metadata.EntryPoint` for tests.

    Real ``EntryPoint.load()`` does an ``importlib.import_module`` on
    a dotted target. The discovery code we're testing only calls
    ``ep.load()`` and reads ``ep.name`` / ``ep.dist``, so a fake
    that returns the factory directly is observationally identical
    while keeping the test self-contained.
    """

    name: str
    factory: Callable[[], Any]
    dist_name: str | None = "toy-overlay-pkg"

    @property
    def dist(self) -> _FakeDist | None:
        return _FakeDist(self.dist_name) if self.dist_name is not None else None

    def load(self) -> Callable[[], Any]:
        return self.factory


class ToyOverlay:
    """Minimal overlay satisfying the :class:`Overlay` protocol.

    Concrete payloads / loaders aren't exercised here — discovery
    only cares that the registered instance structurally types as
    :class:`Overlay`.
    """

    name = "toy"
    schema_version = "0.1"

    def load(self, path: Path) -> None:  # noqa: ARG002 - protocol stub
        return None

    def join(self, graph, annotation) -> None:  # noqa: ARG002 - protocol stub
        return None

    def contribute(self, ctx) -> None:  # noqa: ARG002 - protocol stub
        return None


class ShadowClockOverlay:
    """An external overlay claiming the built-in ``clock`` name."""

    name = "clock"
    schema_version = "9.9"

    def load(self, path: Path) -> None:  # noqa: ARG002
        return None

    def join(self, graph, annotation) -> None:  # noqa: ARG002
        return None

    def contribute(self, ctx) -> None:  # noqa: ARG002
        return None


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, entries: list[Any]) -> None:
    """Replace :func:`importlib.metadata.entry_points` for the
    overlays group with a fixed list of fakes.

    Patches the symbol *as bound inside ``rtl_buddy_view.overlays``*
    because that's the reference :func:`default_registry` reaches
    through; patching :mod:`importlib.metadata` directly wouldn't
    rebind the already-imported alias.
    """

    def fake_entry_points(*, group: str | None = None) -> list[Any]:
        if group == ENTRY_POINT_GROUP:
            return list(entries)
        return []

    monkeypatch.setattr("rtl_buddy_view.overlays.entry_points", fake_entry_points)


# --- happy path -------------------------------------------------------------


def test_external_overlay_is_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    """An external entry point shows up in the registry next to built-ins."""
    _patch_entry_points(monkeypatch, [FakeEntryPoint("toy", ToyOverlay)])

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    assert "toy" in registry.names()
    assert isinstance(registry.get("toy"), ToyOverlay)
    assert isinstance(registry.get("toy"), Overlay)
    assert warn.getvalue() == ""


def test_external_source_is_distribution_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source label = the entry-point's distribution name."""
    _patch_entry_points(
        monkeypatch, [FakeEntryPoint("toy", ToyOverlay, dist_name="cool-pkg")]
    )

    registry = default_registry(warn_stream=io.StringIO())

    assert registry.source_of("toy") == "cool-pkg"
    # Built-ins keep their built-in source even with externals present.
    assert registry.source_of("clock") == BUILTIN_SOURCE
    assert registry.source_of("reset") == BUILTIN_SOURCE


def test_no_externals_no_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero entry points = silent registry with just the built-ins."""
    _patch_entry_points(monkeypatch, [])

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    assert registry.names() == (
        "axi-perf",
        "clock",
        "clock-tb",
        "coverage",
        "hints",
        "reset",
        "wave",
    )
    assert warn.getvalue() == ""


# --- collisions -------------------------------------------------------------


def test_external_colliding_with_builtin_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A built-in name wins; the external is rejected with a warning."""
    _patch_entry_points(
        monkeypatch,
        [FakeEntryPoint("clock", ShadowClockOverlay, dist_name="rogue-pkg")],
    )

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    # Built-in preserved — schema_version isn't 9.9.
    clock = registry.get("clock")
    assert clock.schema_version == "1.0"
    assert registry.source_of("clock") == BUILTIN_SOURCE

    # Warning identifies the shadowing package so the user can act.
    msg = warn.getvalue()
    assert "shadowed" in msg
    assert "rogue-pkg" in msg
    assert "'clock'" in msg


def test_external_colliding_with_external_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two externals on the same name: first wins, second warns + skipped.

    Treated as a warning rather than a fatal error so a single
    misconfigured plugin doesn't break every ``rtl-buddy-view``
    invocation system-wide.
    """
    _patch_entry_points(
        monkeypatch,
        [
            FakeEntryPoint("toy", ToyOverlay, dist_name="first-pkg"),
            FakeEntryPoint("toy", ToyOverlay, dist_name="second-pkg"),
        ],
    )

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    assert registry.source_of("toy") == "first-pkg"
    assert "second-pkg" in warn.getvalue()
    assert "shadowed" in warn.getvalue()


# --- error paths ------------------------------------------------------------


def test_factory_raising_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A factory that raises during instantiation → warn + skip."""

    def boom() -> Any:
        raise RuntimeError("kaboom")

    _patch_entry_points(monkeypatch, [FakeEntryPoint("toy", boom)])

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    assert "toy" not in registry.names()
    msg = warn.getvalue()
    assert "kaboom" in msg
    assert "'toy'" in msg
    assert "failed to instantiate" in msg


def test_load_raising_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """``EntryPoint.load()`` raising (import error) → warn + skip."""

    class FailingEntryPoint:
        name = "toy"
        dist = _FakeDist("toy-overlay-pkg")

        def load(self) -> Any:
            raise ImportError("no module named 'broken_dep'")

    _patch_entry_points(monkeypatch, [FailingEntryPoint()])

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    assert "toy" not in registry.names()
    msg = warn.getvalue()
    assert "failed to import" in msg
    assert "broken_dep" in msg
    assert "'toy'" in msg


def test_non_overlay_factory_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A factory returning something that isn't an Overlay → warn + skip."""

    def not_an_overlay() -> Any:
        return object()

    _patch_entry_points(monkeypatch, [FakeEntryPoint("toy", not_an_overlay)])

    warn = io.StringIO()
    registry = default_registry(warn_stream=warn)

    assert "toy" not in registry.names()
    msg = warn.getvalue()
    assert "not an Overlay" in msg
    assert "'toy'" in msg


# --- dist-name fallback -----------------------------------------------------


def test_entry_point_without_dist_uses_unknown_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An entry point whose ``dist`` is ``None`` falls back to a marker.

    ``EntryPoint.dist`` can legitimately be ``None`` for entry points
    constructed outside a real distribution; we surface the marker
    rather than crashing the discovery path.
    """
    _patch_entry_points(
        monkeypatch, [FakeEntryPoint("toy", ToyOverlay, dist_name=None)]
    )

    registry = default_registry(warn_stream=io.StringIO())

    assert registry.source_of("toy") == UNKNOWN_DIST_SOURCE


# --- registry contract additions --------------------------------------------


def test_source_of_unknown_raises() -> None:
    """``source_of`` mirrors :meth:`get`'s unknown-name behaviour."""
    from rtl_buddy_view.overlays import OverlayError

    registry = default_registry(warn_stream=io.StringIO())
    with pytest.raises(OverlayError, match="unknown overlay 'cov'"):
        registry.source_of("cov")
