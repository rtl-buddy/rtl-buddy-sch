"""Regenerate the Playwright fixtures by re-running the Python producer.

Run from the repo root:

    cd ..  # back out of viewer/
    uv run python viewer/e2e/regen-fixtures.py

Renders each fixture's view.json with the appropriate overlay
combination and writes it under ``viewer/e2e/fixtures/``. The
Playwright suite picks them up at test time via
``page.addInitScript`` — no fetch round-trip, no server side
needed.

Pinned the set of cases in this script (rather than discovering
them from disk) so that adding a new fixture is an intentional
change to the e2e coverage, not a silent expansion.

Heads-up: the four legacy fixtures on disk were written against
``view.json`` v1.0 and a regeneration re-emits them at v1.1 (the
envelope grew ``dut_top`` / ``tb_top``). That is a real, harmless
diff — but it is unrelated to whatever change prompted the regen, so
review it as two things, not one.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

# Find the repo root regardless of where the script is invoked from.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rtl_buddy_view._filelist import parse_filelist  # noqa: E402
from rtl_buddy_view.annotations import load_domain_map  # noqa: E402
from rtl_buddy_view.frontend import Frontend, parse_to_modules  # noqa: E402
from rtl_buddy_view.graph import build_hierarchy  # noqa: E402
from rtl_buddy_view.render import json_render  # noqa: E402
from rtl_buddy_view.reset_annotations import load_reset_domain_map  # noqa: E402

# Stand-in for whatever checkout produced a fixture — see main().
PATH_PLACEHOLDER = "/repo"

# (fixture_dir, top_module, clock_map_or_None, reset_map_or_None, embed_layout)
#
# ``embed_layout`` is what decides whether the fixture carries the
# producer's ``layout`` block. The first four say False on purpose:
# the SPA has two rendering paths, and those fixtures are the ones
# that exercise the *fallback* (build DOT in JS from nodes + edges).
# Turning the block on for them silently moves every snapshot test
# onto the other path. ``block_diagram_demo`` says True because the
# schematic canvas (#163 P2) consumes ``layout.elk``, which only
# exists inside that block — and it is the one fixture whose scopes
# carry real sibling dataflow, so its schematic has wires on it.
CASES = [
    ("two_clock_design", "top", "domain_map.json", None, False),
    ("two_clock_two_reset_design", "top", "clock_map.json", "reset_map.json", False),
    ("counter_with_subs", "counter", None, None, False),
    ("connection_shapes", "top", None, None, False),
    ("block_diagram_demo", "blk_top", None, None, True),
]


def main() -> int:
    out_dir = ROOT / "viewer" / "e2e" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = ROOT / "tests" / "fixtures"
    for name, top, cm_name, rm_name, embed_layout in CASES:
        fix = fixtures_dir / name
        table = parse_to_modules(
            parse_filelist(fix / "files.f"), frontend=Frontend.verible
        )
        root = build_hierarchy(table, top)
        cm = load_domain_map(fix / cm_name) if cm_name else None
        rm = load_reset_domain_map(fix / rm_name) if rm_name else None
        buf = io.StringIO()
        json_render.render(
            root,
            buf,
            domain_map=cm,
            reset_map=rm,
            embed_layout=embed_layout,
            # The table is what makes the renderer emit ``layout.elk``
            # next to ``layout.dot`` — without it the schematic canvas
            # would only ever see its own empty state.
            module_table=table if embed_layout else None,
        )
        # ``source.file`` and the ``rtlbuddy://`` links are absolute by
        # contract, so a regeneration on a different checkout rewrites
        # every one of them and buries the real change in path churn.
        # Rewrite the repo root to a stable placeholder instead.
        text = buf.getvalue().replace(str(ROOT), PATH_PLACEHOLDER)
        (out_dir / f"{name}.json").write_text(text)
        print(f"wrote {out_dir / name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
