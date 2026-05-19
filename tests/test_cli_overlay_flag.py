"""Tests for the Phase 4 ``--overlay name=path`` CLI surface (#17).

Covers the new generalized flag, ``--list-overlays``, and the
legacy-alias deprecation path. Most tests subprocess the
``rtl-buddy-view`` module directly because the value being checked
*is* the CLI behavior end-to-end (argument parsing, stderr text,
exit codes) — unit-testing the underlying helpers wouldn't catch a
typer-options regression.

The renderer behaviour itself is exercised by
``test_clock_overlay.py`` and ``test_reset_overlay.py``; this file
focuses on the dispatch + diagnostics layer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view._verible_install import find_binary

CLOCK_FIXTURES = Path(__file__).parent / "fixtures" / "two_clock_design"
COMBO_FIXTURES = Path(__file__).parent / "fixtures" / "two_clock_two_reset_design"


def _have_verible() -> bool:
    return find_binary("verible-verilog-syntax") is not None


pytestmark_integration = pytest.mark.skipif(
    not _have_verible(),
    reason="verible-verilog-syntax not found on PATH or in vendor/verible/",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", *args],
        check=False,
        capture_output=True,
        text=True,
    )


# --- --list-overlays --------------------------------------------------------


def test_list_overlays_prints_builtin_names() -> None:
    result = _run("--list-overlays")
    assert result.returncode == 0, result.stderr
    # Both built-ins with their schema_version, tab-separated.
    assert "clock\t1.0" in result.stdout
    assert "reset\t1.0" in result.stdout


def test_list_overlays_works_without_top_or_filelist() -> None:
    """``--list-overlays`` is a pure diagnostic — doesn't need a design.

    Pinned because typer's default is "every Option(...) is required";
    we override that for the list-overlays path so users can discover
    the registry without parking a design on the command line.
    """
    result = _run("--list-overlays")
    assert result.returncode == 0
    assert result.stdout.strip() != ""


# --- --overlay name=path (new surface) --------------------------------------


@pytestmark_integration
def test_overlay_clock_renders_clock_domain() -> None:
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--format",
        "tree",
        "--overlay",
        f"clock={CLOCK_FIXTURES / 'domain_map.json'}",
    )
    assert result.returncode == 0, result.stderr
    assert "[clk_a]" in result.stdout
    assert "[clk_b]" in result.stdout


@pytestmark_integration
def test_overlay_clock_and_reset_compose() -> None:
    """Two --overlay flags in one invocation produce the combined overlay."""
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(COMBO_FIXTURES / "files.f"),
        "--format",
        "tree",
        "--overlay",
        f"clock={COMBO_FIXTURES / 'clock_map.json'}",
        "--overlay",
        f"reset={COMBO_FIXTURES / 'reset_map.json'}",
    )
    assert result.returncode == 0, result.stderr
    assert "[clk_a, rst_n↓]" in result.stdout
    assert "⚠RDC[rst_n:async-deassert]" in result.stdout


def test_overlay_unknown_name_lists_known(tmp_path: Path) -> None:
    """A typo in the overlay name surfaces the registered names list."""
    bogus = tmp_path / "anything.json"
    bogus.write_text("{}")
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--overlay",
        f"cov={bogus}",
    )
    assert result.returncode == 1
    assert "unknown overlay 'cov'" in result.stderr
    assert "'clock'" in result.stderr
    assert "'reset'" in result.stderr


def test_overlay_malformed_spec_no_equals() -> None:
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--overlay",
        "this-has-no-equals",
    )
    assert result.returncode == 2
    assert "malformed spec" in result.stderr


def test_overlay_malformed_spec_empty_path() -> None:
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--overlay",
        "clock=",
    )
    assert result.returncode == 2
    assert "malformed spec" in result.stderr


def test_overlay_missing_file_error_uses_name_prefix(tmp_path: Path) -> None:
    """File-not-found surfaces with the overlay name in the prefix."""
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--overlay",
        f"clock={tmp_path / 'nope.json'}",
    )
    assert result.returncode == 1
    assert "overlay clock:" in result.stderr
    assert "file not found" in result.stderr


def test_overlay_duplicate_name_rejected(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    a.write_text("{}")
    b = tmp_path / "b.json"
    b.write_text("{}")
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--overlay",
        f"clock={a}",
        "--overlay",
        f"clock={b}",
    )
    assert result.returncode == 2
    assert "duplicate overlay 'clock'" in result.stderr


# --- deprecated aliases -----------------------------------------------------


@pytestmark_integration
def test_cdc_annotations_alias_emits_deprecation_warning() -> None:
    """``--cdc-annotations`` still works but prints a stderr warning."""
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--format",
        "tree",
        "--cdc-annotations",
        str(CLOCK_FIXTURES / "domain_map.json"),
    )
    assert result.returncode == 0, result.stderr
    assert "--cdc-annotations is deprecated" in result.stderr
    assert "use --overlay clock=PATH" in result.stderr
    # Output is the same as the new flag would produce.
    assert "[clk_a]" in result.stdout


@pytestmark_integration
def test_rdc_annotations_alias_emits_deprecation_warning() -> None:
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(COMBO_FIXTURES / "files.f"),
        "--format",
        "tree",
        "--rdc-annotations",
        str(COMBO_FIXTURES / "reset_map.json"),
    )
    assert result.returncode == 0, result.stderr
    assert "--rdc-annotations is deprecated" in result.stderr
    assert "[rst_n↓]" in result.stdout


def test_alias_and_explicit_overlay_for_same_name_rejected(tmp_path: Path) -> None:
    """``--cdc-annotations X --overlay clock=Y`` is a duplicate."""
    a = tmp_path / "a.json"
    a.write_text("{}")
    b = tmp_path / "b.json"
    b.write_text("{}")
    result = _run(
        "--top",
        "top",
        "--filelist",
        str(CLOCK_FIXTURES / "files.f"),
        "--cdc-annotations",
        str(a),
        "--overlay",
        f"clock={b}",
    )
    assert result.returncode == 2
    assert "duplicate overlay 'clock'" in result.stderr


# --- --top / --filelist still required for actual rendering -----------------


def test_top_required_for_rendering() -> None:
    result = _run("--filelist", str(CLOCK_FIXTURES / "files.f"))
    assert result.returncode == 2
    assert "--top and --filelist are required" in result.stderr
