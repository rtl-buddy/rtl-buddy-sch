"""Smoke tests for the Phase 1 scaffolding.

These cover only what scaffolding alone can guarantee:
- The package imports cleanly.
- The CLI builds and ``--help`` exits 0.
- The frontend factory dispatches to the right (currently stub)
  backend and surfaces a clear ``NotImplementedError``.

The real analyzer-pipeline coverage lands with the Phase 1
implementation — see issue #1.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view.frontend import Frontend, parse_to_modules


def test_package_imports() -> None:
    import rtl_buddy_view  # noqa: F401
    import rtl_buddy_view.cli  # noqa: F401
    import rtl_buddy_view.extractor  # noqa: F401
    import rtl_buddy_view.frontend  # noqa: F401
    import rtl_buddy_view.graph  # noqa: F401
    import rtl_buddy_view.query  # noqa: F401
    import rtl_buddy_view.render.dot  # noqa: F401
    import rtl_buddy_view.render.tree  # noqa: F401


def test_cli_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    # We don't ship a __main__.py yet; fall back to the typer app
    # via the installed entry point if -m fails.
    if result.returncode != 0:
        result = subprocess.run(
            ["rtl-buddy-view", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
    assert result.returncode == 0, result.stderr
    assert "rtl-buddy-view" in result.stdout.lower() or "top" in result.stdout.lower()


def test_version_flag_prints_parseable_version() -> None:
    """``--version`` is the contract rtl_buddy probes to enforce a floor.

    The output is ``rtl-buddy-view <X.Y.Z>``; downstream consumers
    extract the version with ``r"rtl-buddy-view\\s+(\\d+\\.\\d+\\.\\d+)"``.
    Keep this test in lockstep with that regex.
    """
    result = subprocess.run(
        [sys.executable, "-m", "rtl_buddy_view", "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"rtl-buddy-view\s+(\d+\.\d+\.\d+)", result.stdout)
    assert match is not None, f"unexpected --version output: {result.stdout!r}"


def test_slang_frontend_stub_signals_clearly(tmp_path: Path) -> None:
    """The slang frontend is a Phase 2 feature; the stub must say so."""
    files = [tmp_path / "dummy.sv"]
    for f in files:
        f.write_text("module dummy; endmodule\n")
    with pytest.raises(NotImplementedError):
        parse_to_modules(files, frontend=Frontend.slang)
