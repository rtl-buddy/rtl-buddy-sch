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

# (fixture_dir, top_module, clock_map_filename_or_None, reset_map_filename_or_None)
CASES = [
    ("two_clock_design", "top", "domain_map.json", None),
    ("two_clock_two_reset_design", "top", "clock_map.json", "reset_map.json"),
    ("counter_with_subs", "counter", None, None),
    ("connection_shapes", "top", None, None),
]


def main() -> int:
    out_dir = ROOT / "viewer" / "e2e" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir = ROOT / "tests" / "fixtures"
    for name, top, cm_name, rm_name in CASES:
        fix = fixtures_dir / name
        table = parse_to_modules(
            parse_filelist(fix / "files.f"), frontend=Frontend.verible
        )
        root = build_hierarchy(table, top)
        cm = load_domain_map(fix / cm_name) if cm_name else None
        rm = load_reset_domain_map(fix / rm_name) if rm_name else None
        buf = io.StringIO()
        json_render.render(root, buf, domain_map=cm, reset_map=rm)
        (out_dir / f"{name}.json").write_text(buf.getvalue())
        print(f"wrote {out_dir / name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
