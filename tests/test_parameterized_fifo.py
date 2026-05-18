"""End-to-end tests for parameter extraction and overrides.

The ``fifo`` fixture has two declared parameters (``WIDTH``, ``DEPTH``)
and two child instances that exercise the override surface:

- ``u_core`` passes both overrides
- ``u_ptr`` overrides only ``WIDTH``, leaving ``DEPTH`` at the child's
  implicit default (the child itself is a blackbox in this fixture
  but the override is still captured on the parent side)
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
from rtl_buddy_view.render import dot as dot_render

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "parameterized_fifo"


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


def test_extracts_module_parameters_with_defaults() -> None:
    fifo = _table().modules_by_name["fifo"]
    assert [(p.name, p.default_text) for p in fifo.parameters] == [
        ("WIDTH", "8"),
        ("DEPTH", "16"),
    ]


def test_parameter_locations_point_at_the_name_token() -> None:
    width = _table().modules_by_name["fifo"].parameters[0]
    assert width.location is not None
    # WIDTH appears on line 9 (six comment lines + module header line).
    assert width.location.start_line == 9


def test_instance_carries_named_overrides() -> None:
    fifo = _table().modules_by_name["fifo"]
    overrides = {
        i.name: [(ov.param_name, ov.value_text) for ov in i.param_overrides]
        for i in fifo.instances
    }
    assert overrides == {
        "u_core": [("WIDTH", "WIDTH"), ("DEPTH", "DEPTH")],
        "u_ptr": [("WIDTH", "16")],
    }


def test_dot_renders_param_overrides_in_label() -> None:
    root = build_hierarchy(_table(), "fifo")
    buf = io.StringIO()
    dot_render.render(root, buf)
    output = buf.getvalue()
    # Header / opening / closing structure.
    assert output.startswith("digraph hierarchy {\n")
    assert output.rstrip().endswith("}")
    # Root node has no overrides on itself.
    assert '"fifo"' in output
    # Children with their overrides surfaced as part of the label.
    assert ".WIDTH(WIDTH)" in output
    assert ".DEPTH(DEPTH)" in output
    assert ".WIDTH(16)" in output
    # Both children are blackbox → dashed style appears.
    assert "dashed" in output
    # Edges exist from root to each child.
    assert '"fifo" -> "fifo.u_core";' in output
    assert '"fifo" -> "fifo.u_ptr";' in output


def test_cli_dot_end_to_end() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtl_buddy_view",
            "--top",
            "fifo",
            "--filelist",
            str(FIXTURE_DIR / "files.f"),
            "--format",
            "dot",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("digraph hierarchy {\n")
    assert "fifo.u_core" in result.stdout
