"""The installed distribution's version, in one place.

Three artefacts stamp a generator version into their payloads
(``view.json``, ``graph.json``, the ELK schematic), and each grew its
own ``importlib.metadata`` lookup. Two of them still asked for
``rtl-buddy-view`` — the name this project shipped under up to 0.5.0
— so after the rename every stamp silently degraded to the
``"0.0.0"`` floor: the title block on a schematic printed
``rtl-buddy-sch 0.0.0`` next to a 0.9.0 install, and ``graph.json``
recorded the same fiction.

A floor that reads like a real version is the trap. Keep the lookup
in one function so a rename can only ever be wrong once.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version

#: Distribution names to try, newest first. The second is the
#: pre-0.7.0 name: an install made before the rename still stamps a
#: real version instead of the floor.
DIST_NAMES: tuple[str, ...] = ("rtl-buddy-sch", "rtl-buddy-view")

#: What a source tree with no installed metadata at all reports. It
#: must stay a *valid* version string — every consumer schema
#: requires one — which is exactly why it must never be reachable
#: from a real install.
UNKNOWN_VERSION = "0.0.0"


def dist_version() -> str:
    """This distribution's version, or :data:`UNKNOWN_VERSION`."""
    for dist in DIST_NAMES:
        try:
            return _metadata_version(dist)
        except PackageNotFoundError:
            continue
    return UNKNOWN_VERSION
