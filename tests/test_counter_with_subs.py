"""End-to-end tests for the ports / instances slice.

Exercises a small two-level hierarchy: ``counter`` instantiates
``counter_ff`` (defined in the filelist) and ``sub_x`` (deliberately
absent — should become a blackbox leaf in the rendered tree).
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._filelist import parse_filelist
from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.graph import build_hierarchy
from rtl_buddy_view.render import tree as tree_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "counter_with_subs"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _table():
    return parse_to_modules(
        parse_filelist(FIXTURE_DIR / "files.f"), frontend=Frontend.verible
    )


def test_extracts_ports_with_directions() -> None:
    table = _table()
    counter = table.modules_by_name["counter"]
    assert [(p.name, p.direction) for p in counter.ports] == [
        ("clk", "input"),
        ("rst_n", "input"),
        ("q", "output"),
    ]
    counter_ff = table.modules_by_name["counter_ff"]
    assert [(p.name, p.direction) for p in counter_ff.ports] == [
        ("clk", "input"),
        ("q", "output"),
    ]


def test_port_type_text_is_captured() -> None:
    counter = _table().modules_by_name["counter"]
    # We don't pin the exact whitespace shape — just confirm the
    # ``logic`` keyword is preserved verbatim from source.
    for port in counter.ports:
        assert port.type_text is not None
        assert "logic" in port.type_text


def test_port_locations_point_at_the_port_name() -> None:
    counter = _table().modules_by_name["counter"]
    clk = next(p for p in counter.ports if p.name == "clk")
    assert clk.location is not None
    # ``clk`` is on line 6 of counter.sv (after the 4-line comment and
    # the module-header line).
    assert clk.location.start_line == 6


def test_extracts_instances_with_module_names() -> None:
    counter = _table().modules_by_name["counter"]
    assert [(i.name, i.module_name) for i in counter.instances] == [
        ("u_ff", "counter_ff"),
        ("u_x", "sub_x"),
    ]
    assert _table().modules_by_name["counter_ff"].instances == ()


def test_hierarchy_marks_undefined_as_blackbox() -> None:
    table = _table()
    root = build_hierarchy(table, "counter")
    assert root.module_name == "counter"
    assert len(root.children) == 2
    ff, x = root.children
    assert ff.instance_path == "counter.u_ff"
    assert ff.module_name == "counter_ff"
    assert ff.is_blackbox is False
    assert x.instance_path == "counter.u_x"
    assert x.module_name == "sub_x"
    assert x.is_blackbox is True
    assert "sub_x" in table.unresolved


def test_render_tree_two_level_hierarchy() -> None:
    root = build_hierarchy(_table(), "counter")
    buf = io.StringIO()
    tree_render.render(root, buf)
    assert buf.getvalue() == (
        "counter\n├── u_ff : counter_ff\n└── u_x : sub_x (blackbox)\n"
    )


def test_cli_renders_full_tree() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "counter",
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
    assert result.stdout == (
        "counter\n├── u_ff : counter_ff\n└── u_x : sub_x (blackbox)\n"
    )
