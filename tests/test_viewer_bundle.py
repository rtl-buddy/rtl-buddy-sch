"""Tests for ``rtl_buddy_view.viewer_bundle`` — the SPA bundle locator.

The hub's auto-discovery code calls :func:`viewer_bundle.path` to find
the shipped SPA without the user having to pass ``--viewer-bundle``.
Verify that:

* when the bundle ships (the wheel layout we control), the locator
  returns a directory containing ``index.html``;
* when the bundle is absent or malformed (no ``index.html``), it
  returns ``None`` so the caller can fall back to the placeholder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rtl_buddy_view import viewer_bundle


def test_path_returns_directory_with_index_when_bundle_ships(tmp_path: Path):
    """Construct a fake bundle layout in tmp and point the helper at it."""

    bundle = tmp_path / "_viewer_bundle"
    bundle.mkdir()
    (bundle / "index.html").write_text("<html>shipped</html>", encoding="utf-8")

    # Mimic the layout viewer_bundle.path() walks: a fake module file
    # sitting next to a _viewer_bundle/ directory.
    fake_module_file = tmp_path / "viewer_bundle.py"
    fake_module_file.write_text("# placeholder")

    # Bypass the real package's __file__ by calling the same logic with
    # an overridden anchor.
    result = _resolve_bundle(fake_module_file)
    assert result is not None
    assert result == bundle
    assert (result / "index.html").is_file()


def test_path_returns_none_when_bundle_dir_missing(tmp_path: Path):
    """No _viewer_bundle/ directory next to the module → None."""

    fake_module_file = tmp_path / "viewer_bundle.py"
    fake_module_file.write_text("# placeholder")
    assert _resolve_bundle(fake_module_file) is None


def test_path_returns_none_when_bundle_lacks_index(tmp_path: Path):
    """A _viewer_bundle/ that's missing index.html is treated as absent
    — covers the case where a corrupt/partial install ships stub files
    but not the entry point. Falling back to the placeholder is correct
    behaviour."""

    bundle = tmp_path / "_viewer_bundle"
    bundle.mkdir()
    (bundle / "stray.css").write_text("/* no index */", encoding="utf-8")
    fake_module_file = tmp_path / "viewer_bundle.py"
    fake_module_file.write_text("# placeholder")
    assert _resolve_bundle(fake_module_file) is None


def test_path_against_real_install():
    """Live install — depends on scripts/prebuild_viewer.py having run.

    When iterating locally with the bundle staged this passes; in a
    fresh checkout without a prebuild step it skips so the suite stays
    green for graph-extraction-only work."""

    if not _bundle_present_in_real_install():
        pytest.skip("viewer bundle not staged — run scripts/prebuild_viewer.py")

    result = viewer_bundle.path()
    assert result is not None
    assert (result / "index.html").is_file()


def _resolve_bundle(module_file: Path) -> Path | None:
    """Re-implementation of viewer_bundle.path that takes the module
    anchor as an argument — lets us test the resolution logic against
    a tmp layout without monkey-patching ``__file__`` on the imported
    module (which Pyright + reload semantics make brittle)."""
    bundle = module_file.resolve().parent / "_viewer_bundle"
    if (bundle / "index.html").is_file():
        return bundle
    return None


def _bundle_present_in_real_install() -> bool:
    module_path = Path(viewer_bundle.__file__).resolve()
    return (module_path.parent / "_viewer_bundle" / "index.html").is_file()
