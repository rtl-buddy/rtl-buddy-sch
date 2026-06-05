"""Regenerate the Phase 3 reset-overlay goldens.

Run from the repo root::

    uv run python tests/regen_goldens.py

The byte-for-byte golden test in ``test_reset_overlay.py`` pins the
output shape of every renderer against the headline acceptance
fixture (two-clock / two-reset / one-RDC). Cosmetic changes to any
renderer fail that test; regenerate goldens *deliberately* with this
script after reviewing the rendered diff.

The JSON ``location.file`` values are normalised to a
fixture-relative form on the way out — Verible reports absolute
paths and we don't want a machine-specific filesystem layout
baked into the goldens.

Keep this script intentionally minimal — it's the operator surface
for an intentional schema/style change, not a CI hook.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view.annotations import load_domain_map
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot, json_render, mermaid, tree
from rtl_buddy_view.reset_annotations import load_reset_domain_map


def _normalize_paths(text: str) -> str:
    """Strip the absolute-path prefix from every Verible-reported path.

    Normalises both the ``"file":`` JSON key and the ``link`` URI's
    ``file=`` query parameter, since both carry Verible's
    machine-absolute path verbatim. Must stay in sync with the
    equivalent helper in ``test_reset_overlay.py``.
    """
    text = re.sub(
        r'"file":\s*"[^"]*?/tests/fixtures/',
        '"file": "tests/fixtures/',
        text,
    )
    text = re.sub(
        r"rtlbuddy://open\?file=[^&\"]*?/tests/fixtures/",
        "rtlbuddy://open?file=tests/fixtures/",
        text,
    )
    # tool.version is derived from the git tag at build time (hatch-vcs),
    # so it differs between an editable dev checkout and a tagged release
    # build. Normalise it so the golden pins content, not the build's
    # version string. Must stay in sync with the helper in
    # tests/test_reset_overlay.py.
    text = re.sub(
        r'("name":\s*"rtl-buddy-view",\s*"version":\s*)"[^"]*"',
        r'\1"<version>"',
        text,
    )
    return text


def main() -> None:
    fix = Path("tests/fixtures/two_clock_two_reset_design")
    table = parse_to_modules(parse_filelist(fix / "files.f"), frontend=Frontend.verible)
    root = build_hierarchy(table, "top")
    cm = load_domain_map(fix / "clock_map.json")
    rm = load_reset_domain_map(fix / "reset_map.json")
    gold = fix / "goldens"
    gold.mkdir(exist_ok=True)
    targets = [
        ("tree.txt", tree.render),
        ("hierarchy.dot", dot.render),
        ("hierarchy.mmd", mermaid.render),
        ("hierarchy.json", json_render.render),
    ]
    for name, render_fn in targets:
        buf = io.StringIO()
        render_fn(root, buf, domain_map=cm, reset_map=rm)
        (gold / name).write_text(_normalize_paths(buf.getvalue()))
        print(f"wrote {gold / name}")


if __name__ == "__main__":
    main()
