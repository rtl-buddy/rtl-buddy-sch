"""End-to-end coverage for the ``rbsch`` pragma CLI surface (#159).

The unit tests in ``test_hints.py`` build their own ``ModuleTable``,
so they can't prove the one thing most likely to break silently:
that the path string :func:`rtl_buddy_view.hints.scan_pragmas` is
keyed by is byte-identical to the ``SourceLocation.file`` the Verible
frontend records. If those ever diverge, every pragma resolves to
nothing and the only symptom is a diagram that ignores its author.
These tests are therefore Verible-gated and drive the real CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary

FIXTURES = Path(__file__).parent / "fixtures" / "pragma_demo"
FILELIST = FIXTURES / "files.f"

pytestmark = pytest.mark.skipif(
    find_binary("verible-verilog-syntax") is None,
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _render(*args: str) -> subprocess.CompletedProcess[str]:
    return _run("--top", "prg_top", "--filelist", str(FILELIST), *args)


def _node_paths(payload: dict) -> list[str]:
    return [n["id"] for n in payload["nodes"]]


# --- association proven end to end ------------------------------------------


def test_pragmas_are_scanned_by_default_in_every_format() -> None:
    """The graph transform is renderer-independent — JSON is the
    machine-readable witness for what tree / dot / mermaid also see."""
    result = _render("--format", "json")
    assert result.returncode == 0, result.stderr
    paths = _node_paths(json.loads(result.stdout))
    assert paths == [
        "prg_top",
        "prg_top.u_csr",
        "prg_top.u_csr.u_sync",
        "prg_top.u_engine",
    ]


def test_no_pragmas_restores_the_design_as_written() -> None:
    result = _render("--format", "json", "--no-pragmas")
    assert result.returncode == 0, result.stderr
    paths = _node_paths(json.loads(result.stdout))
    # hide → u_tieoff back; collapse → u_engine's stages back;
    # module leaf → prg_sync's flop back.
    assert "prg_top.u_tieoff" in paths
    assert "prg_top.u_engine.u_stage0" in paths
    assert "prg_top.u_csr.u_sync.u_ff0" in paths


def test_clean_fixture_produces_no_hint_warnings() -> None:
    result = _render("--format", "json")
    assert "hints:" not in result.stderr


def test_hidden_and_collapsed_nodes_are_gone_from_the_tree_too() -> None:
    result = _render("--format", "tree")
    assert result.returncode == 0, result.stderr
    assert "u_csr" in result.stdout
    assert "u_tieoff" not in result.stdout
    assert "u_stage0" not in result.stdout
    assert "u_ff0" not in result.stdout


def test_labels_render_in_dot_ahead_of_the_instance_name() -> None:
    result = _render("--format", "dot")
    assert result.returncode == 0, result.stderr
    assert r"CSR block\lu_csr\lprg_csr\l" in result.stdout
    assert r"ALU datapath\lu_engine\lprg_engine\l" in result.stdout


def test_labels_render_in_block_mode_html_labels() -> None:
    result = _render("--format", "dot", "--block-diagram")
    assert result.returncode == 0, result.stderr
    assert "<B>CSR block</B>" in result.stdout
    assert "<B>ALU datapath</B>" in result.stdout


def test_json_output_gains_no_new_keys() -> None:
    """``display_label`` is deliberately not in view.json (phase 1)."""
    payload = json.loads(_render("--format", "json").stdout)
    assert all("display_label" not in node for node in payload["nodes"])


# --- sidecar overlay --------------------------------------------------------


def test_sidecar_overrides_the_in_source_pragma() -> None:
    result = _render(
        "--format", "json", "--overlay", f"hints={FIXTURES / 'hints.json'}"
    )
    assert result.returncode == 0, result.stderr
    paths = _node_paths(json.loads(result.stdout))
    # The sidecar revokes u_engine's in-source `collapse`.
    assert "prg_top.u_engine.u_stage0" in paths
    # …while leaving the pragma-only hide + module leaf in force.
    assert "prg_top.u_tieoff" not in paths
    assert "prg_top.u_csr.u_sync.u_ff0" not in paths


def test_sidecar_label_beats_the_in_source_label() -> None:
    result = _render("--format", "dot", "--overlay", f"hints={FIXTURES / 'hints.json'}")
    assert "engine (sidecar)" in result.stdout
    assert "ALU datapath" not in result.stdout


def test_sidecar_applies_with_pragma_scanning_disabled() -> None:
    result = _render(
        "--format",
        "dot",
        "--no-pragmas",
        "--overlay",
        f"hints={FIXTURES / 'hints.json'}",
    )
    assert result.returncode == 0, result.stderr
    assert "engine (sidecar)" in result.stdout
    # `--no-pragmas` is scoped to in-source scanning only.
    assert "CSR block" not in result.stdout


def test_malformed_sidecar_exits_1_with_the_overlay_prefix(tmp_path: Path) -> None:
    bad = tmp_path / "hints.json"
    bad.write_text('{"schema_version": "9.0"}')
    result = _render("--format", "json", "--overlay", f"hints={bad}")
    assert result.returncode == 1
    assert "overlay hints:" in result.stderr


def test_hints_overlay_is_listed() -> None:
    result = _run("--list-overlays")
    assert "hints\t1.0\t(built-in)" in result.stdout


# --- warnings ---------------------------------------------------------------


def test_unknown_pragma_key_warns_on_stderr_and_still_renders(
    tmp_path: Path,
) -> None:
    src = tmp_path / "warn_top.sv"
    src.write_text(
        "module warn_top;\n"
        "  // rbsch: collpase\n"
        "  warn_sub u_sub ();\n"
        "endmodule\n"
        "module warn_sub;\n"
        "endmodule\n"
    )
    filelist = tmp_path / "files.f"
    filelist.write_text("warn_top.sv\n")
    result = _run("--top", "warn_top", "--filelist", str(filelist), "--format", "tree")
    assert result.returncode == 0, result.stderr
    assert "hints: " in result.stderr
    assert "collpase" in result.stderr
    assert "collapse, hide, leaf" in result.stderr
    assert "u_sub" in result.stdout
