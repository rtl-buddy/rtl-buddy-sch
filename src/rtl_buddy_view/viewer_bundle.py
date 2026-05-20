"""Locate the bundled Vue SPA shipped inside this wheel.

``rtl_buddy``'s hub looks the bundle up via :func:`path` so users can run
``rb hub start --serve-viewer`` without passing ``--viewer-bundle``.
Returns ``None`` when no bundle ships in this install — e.g. when running
from a fresh checkout without ``scripts/prebuild_viewer.py`` having been
run, or from a wheel built without the SPA. Callers fall back to the
hub's placeholder page in that case.
"""

from __future__ import annotations

from pathlib import Path

_BUNDLE_DIR = "_viewer_bundle"


def path() -> Path | None:
    """Return the on-disk path of the SPA bundle, or ``None`` if absent.

    The returned path is suitable for passing to a static file server
    that serves a directory of arbitrary assets. ``index.html`` is
    guaranteed to exist under the returned path when the result is
    non-``None``.

    Wheels are unzipped into site-packages so ``__file__`` resolution
    is reliable here; zip imports (rare for this package) would need
    ``importlib.resources.as_file``, which we can wire in if a user
    reports it.
    """
    bundle = Path(__file__).resolve().parent / _BUNDLE_DIR
    if (bundle / "index.html").is_file():
        return bundle
    return None
