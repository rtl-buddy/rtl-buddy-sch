"""Phase 4 acceptance test: every parseable fixture's view.json
output validates against ``schemas/view-v1.json``.

Pins the issue's Phase 4 acceptance criterion: ``--format json``
must emit a payload that validates against the locked schema for
all Phase 1 + Phase 2 + Phase 3 fixtures. The shape tests in
``test_render_json.py`` exercise individual fields; this test
exercises the full hierarchy emitted by Verible end-to-end.

Skipped when verible-verilog-syntax isn't on the path / vendored
— the JSON renderer's input is a real verible parse, not a
synthetic graph.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import jsonschema
import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.annotations import load_domain_map
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import json_render
from rtl_buddy_view.reset_annotations import load_reset_domain_map

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "view-v1.json"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


@pytest.fixture(scope="module")
def view_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


# Each parametrise entry: (fixture_dir, top_module, clock_map?, reset_map?).
# Clock/reset map filenames are only present when the fixture
# carries those maps; the renderer accepts ``None`` for either.
_FIXTURE_CASES = [
    ("empty_module", "empty", None, None),
    ("counter_with_subs", "counter", None, None),
    ("parameterized_fifo", "fifo", None, None),
    ("connection_shapes", "top", None, None),
    ("two_clock_design", "top", "domain_map.json", None),
    (
        "two_clock_two_reset_design",
        "top",
        "clock_map.json",
        "reset_map.json",
    ),
    ("two_reset_with_rdc", "top", "clock_map.json", "reset_map.json"),
    ("reset_synchronizer_chain", "top", None, "reset_map.json"),
]


@pytest.mark.parametrize(
    "fixture_dir,top,clock_map,reset_map",
    _FIXTURE_CASES,
    ids=[case[0] for case in _FIXTURE_CASES],
)
def test_fixture_json_output_validates(
    view_schema: dict,
    fixture_dir: str,
    top: str,
    clock_map: str | None,
    reset_map: str | None,
) -> None:
    fix = FIXTURES_ROOT / fixture_dir
    files = parse_filelist(fix / "files.f")
    table = parse_to_modules(files, frontend=Frontend.verible)
    root = build_hierarchy(table, top)

    dm = load_domain_map(fix / clock_map) if clock_map else None
    rm = load_reset_domain_map(fix / reset_map) if reset_map else None

    buf = io.StringIO()
    json_render.render(root, buf, domain_map=dm, reset_map=rm)
    payload = json.loads(buf.getvalue())
    jsonschema.validate(instance=payload, schema=view_schema)

    # Sanity: the envelope always reports the requested top.
    assert payload["top"] == top
    # Stable IDs: every node id starts with the top module name.
    for node in payload["nodes"]:
        assert node["id"] == top or node["id"].startswith(top + ".")
