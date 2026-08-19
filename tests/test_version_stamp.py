"""Every payload stamps the *installed* version, from one lookup.

Three artefacts carry a generator version — ``view.json``,
``graph.json`` and the ELK schematic payload — and each once had its
own ``importlib.metadata`` call. Two asked for the pre-0.7.0
distribution name, so after the rename they silently degraded to the
``"0.0.0"`` floor: a schematic title block printed
``rtl-buddy-sch 0.0.0`` next to a 0.9.0 install.

The floor has to stay a valid version string, which is what made the
failure quiet — so these tests pin the *agreement* between the stamps
and the package, not the string.
"""

from __future__ import annotations

import rtl_buddy_view
from rtl_buddy_view._dist import DIST_NAMES, UNKNOWN_VERSION, dist_version
from rtl_buddy_view.elk_export import _version as elk_version
from rtl_buddy_view.graph_export import _version as graph_version
from rtl_buddy_view.render.json_render import _version as json_version


def test_every_stamp_agrees_with_the_package_version() -> None:
    stamps = {
        "view.json": json_version(),
        "graph.json": graph_version(),
        "elk": elk_version(),
        "helper": dist_version(),
    }
    assert set(stamps.values()) == {rtl_buddy_view.__version__}


def test_the_installed_version_is_not_the_unknown_floor() -> None:
    """The test suite runs against an installed distribution, so a
    stamp reaching the floor means the lookup missed the dist name —
    the exact regression this module exists for."""
    assert dist_version() != UNKNOWN_VERSION


def test_the_pre_rename_name_is_still_tried() -> None:
    """Installs made before the 0.7.0 rename must keep stamping a
    real version rather than falling to the floor."""
    assert DIST_NAMES[0] == "rtl-buddy-sch"
    assert "rtl-buddy-view" in DIST_NAMES
