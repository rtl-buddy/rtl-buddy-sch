"""Regenerate the Phase 3 reset-overlay goldens.

Run from the repo root::

    uv run python tests/regen_goldens.py

The byte-for-byte golden test in ``test_reset_overlay.py`` pins the
output shape of every renderer against the headline acceptance
fixture (two-clock / two-reset / one-RDC). Cosmetic changes to any
renderer fail that test; regenerate goldens *deliberately* with this
script after reviewing the rendered diff.

Keep this script intentionally minimal — it's the operator surface
for an intentional schema/style change, not a CI hook.
"""

from __future__ import annotations

import io
from pathlib import Path

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view.annotations import load_domain_map
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import dot, json_render, mermaid, tree
from rtl_buddy_view.reset_annotations import load_reset_domain_map


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
        (gold / name).write_text(buf.getvalue())
        print(f"wrote {gold / name}")


if __name__ == "__main__":
    main()
