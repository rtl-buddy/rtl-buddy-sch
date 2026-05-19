"""Tests for the Phase 4 overlay plugin layer (#17).

Covers the registry's name → plugin dispatch, error paths for
unknown names + duplicate registration, and the default-registry
contents (the two built-ins — clock and reset). The plugins
themselves are thin wrappers over the Phase 2/3 loaders, which
already have exhaustive coverage in ``test_annotations.py`` and
``test_reset_annotations.py``; we don't re-test the loaders here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rtl_buddy_view.annotations import DomainMap
from rtl_buddy_view.overlays import (
    Overlay,
    OverlayError,
    OverlayRegistry,
    default_registry,
)
from rtl_buddy_view.overlays.clock import ClockOverlay
from rtl_buddy_view.overlays.reset import ResetOverlay
from rtl_buddy_view.reset_annotations import ResetDomainMap

CLOCK_FIXTURES = Path(__file__).parent / "fixtures" / "domain_maps"
RESET_FIXTURES = Path(__file__).parent / "fixtures" / "reset_domain_maps"


# --- registry behaviour -----------------------------------------------------


def test_default_registry_has_clock_and_reset() -> None:
    registry = default_registry()
    assert registry.names() == ("clock", "reset")


def test_get_returns_registered_overlay() -> None:
    registry = default_registry()
    clock = registry.get("clock")
    assert isinstance(clock, ClockOverlay)
    reset = registry.get("reset")
    assert isinstance(reset, ResetOverlay)


def test_get_unknown_raises_with_known_list() -> None:
    registry = default_registry()
    with pytest.raises(
        OverlayError, match=r"unknown overlay 'cov'.*\['clock', 'reset'\]"
    ):
        registry.get("cov")


def test_register_duplicate_raises() -> None:
    """Two overlays under the same name confuse CLI diagnostics — reject."""
    registry = OverlayRegistry()
    registry.register(ClockOverlay())
    with pytest.raises(OverlayError, match="already registered"):
        registry.register(ClockOverlay())


def test_iteration_is_name_sorted() -> None:
    """``overlays_present`` and ``--list-overlays`` both need stable order."""
    registry = default_registry()
    yielded = [o.name for o in registry]
    assert yielded == sorted(yielded)
    assert yielded == ["clock", "reset"]


# --- protocol structural typing ---------------------------------------------


def test_builtins_satisfy_overlay_protocol() -> None:
    """Both built-ins are structurally :class:`Overlay`.

    The protocol uses ``runtime_checkable`` so the registry could
    enforce this at register-time; pinning it here documents the
    contract for third-party overlay authors.
    """
    assert isinstance(ClockOverlay(), Overlay)
    assert isinstance(ResetOverlay(), Overlay)


# --- built-in overlays -------------------------------------------------------


def test_clock_overlay_load_returns_domain_map() -> None:
    overlay = ClockOverlay()
    assert overlay.name == "clock"
    assert overlay.schema_version == "1.0"
    result = overlay.load(CLOCK_FIXTURES / "two_domain_with_crossing.json")
    assert isinstance(result, DomainMap)
    assert result.design_top == "top"


def test_reset_overlay_load_returns_reset_domain_map() -> None:
    overlay = ResetOverlay()
    assert overlay.name == "reset"
    assert overlay.schema_version == "1.0"
    result = overlay.load(RESET_FIXTURES / "bad_marked_reset_polarity.json")
    assert isinstance(result, ResetDomainMap)
    assert result.design_top == "bad_marked_reset_polarity"


def test_clock_join_and_contribute_are_no_ops() -> None:
    """Phase 4 keeps the existing direct-lookup pattern.

    Renderers reach into :meth:`DomainMap.predominant_clock` etc.
    directly, so :meth:`join` and :meth:`contribute` don't mutate
    anything. Pinned so a future contributor doesn't accidentally
    rely on them as a side-effect channel.
    """
    overlay = ClockOverlay()
    # Both should accept arbitrary inputs and return None without
    # raising; we don't need a real graph/annotation here.
    assert overlay.join(None, None) is None  # type: ignore[arg-type]
    assert overlay.contribute(None) is None


def test_reset_join_and_contribute_are_no_ops() -> None:
    overlay = ResetOverlay()
    assert overlay.join(None, None) is None  # type: ignore[arg-type]
    assert overlay.contribute(None) is None
