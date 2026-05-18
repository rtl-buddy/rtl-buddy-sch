"""End-to-end test for the Phase 1 vertical slice.

Parses the ``empty_module`` fixture through the real Verible binary,
builds the hierarchy, renders the ASCII tree, and asserts the output
matches a one-line golden. This is the smallest possible coverage
that exercises every layer (filelist → frontend → extractor →
graph → renderer) end-to-end.

Skips automatically if no Verible binary is on PATH and the vendored
copy isn't installed — local runs without ``brew install verible``
or ``scripts/fetch_verible.py`` should not be considered failures.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import tree as tree_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "empty_module"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def test_parse_extracts_empty_module() -> None:
    table = parse_to_modules([FIXTURE_DIR / "empty.sv"], frontend=Frontend.verible)
    assert set(table.modules_by_name) == {"empty"}
    mod = table.modules_by_name["empty"]
    assert mod.ports == ()
    assert mod.parameters == ()
    assert mod.instances == ()
    assert mod.location is not None
    assert mod.location.file.endswith("empty.sv")
    # ``module`` keyword starts on line 4 (lines 1-3 are the comment
    # block); guards against the offset-translation helper regressing.
    assert mod.location.start_line == 4


def test_build_hierarchy_single_module() -> None:
    table = parse_to_modules([FIXTURE_DIR / "empty.sv"], frontend=Frontend.verible)
    root = build_hierarchy(table, "empty")
    assert root.module_name == "empty"
    assert root.instance_path == "empty"
    assert root.instance is None
    assert root.is_blackbox is False
    assert root.children == ()


def test_render_tree_single_module() -> None:
    table = parse_to_modules([FIXTURE_DIR / "empty.sv"], frontend=Frontend.verible)
    root = build_hierarchy(table, "empty")
    buf = io.StringIO()
    tree_render.render(root, buf)
    assert buf.getvalue() == "empty\n"


def test_cli_end_to_end() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "empty",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--format",
            "tree",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "empty\n"


def test_cli_unknown_top_exits_with_hint(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "no_such_module",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "not found" in result.stderr.lower()
    assert "empty" in result.stderr  # the known-modules hint should list it
