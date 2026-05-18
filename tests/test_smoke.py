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

import subprocess
import sys
from pathlib import Path

import pytest

from rtl_buddy_view.frontend import Frontend, parse_to_modules
from rtl_buddy_view.frontend.verible import VeribleUnavailable


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


def test_frontend_stubs_signal_clearly(tmp_path: Path) -> None:
    """Both frontends should fail loudly while Phase 1 is in flight.

    The verible frontend probes the binary up-front: when verible is
    installed the call surfaces ``NotImplementedError`` (the analyzer
    isn't built yet); when it isn't installed we surface
    :class:`VeribleUnavailable` with an install hint. Either is a
    valid scaffolding state; both should not silently swallow input.
    """
    files = [tmp_path / "dummy.sv"]
    for f in files:
        f.write_text("module dummy; endmodule\n")
    with pytest.raises((NotImplementedError, VeribleUnavailable)):
        parse_to_modules(files, frontend=Frontend.verible)
    with pytest.raises(NotImplementedError):
        parse_to_modules(files, frontend=Frontend.slang)
