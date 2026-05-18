"""Verible frontend — subprocess wrapper around ``verible-verilog-syntax``.

The full Phase 1 implementation will:

1. Locate the Verible binary (pinned release fetched by the setup
   flow into ``vendor/verible/``, or on ``PATH`` for dev installs).
   Handled by :func:`locate_binary` below.
2. Run ``verible-verilog-syntax --export_json <file>`` per source file.
3. Cache the resulting JSON CST keyed on the file's content hash, so
   reparsing on every query is avoided.
4. Walk the CST via Verible's ``verible_verilog_syntax`` Python helper
   to produce :class:`rtl_buddy_view.extractor.Module` instances.

Phase 1 issue: [rtl-buddy/rtl-buddy-view#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view._verible_install import find_binary
from rtl_buddy_view.extractor import ModuleTable


class VeribleUnavailable(RuntimeError):
    """Raised when the verible-verilog-syntax binary can't be found."""


def locate_binary(name: str = "verible-verilog-syntax") -> Path:
    """Return the path to a verible-* binary or raise :class:`VeribleUnavailable`.

    Resolution order: ``PATH`` first (so Homebrew-installed Verible
    or whatever's already on the system wins), then the vendored copy
    under ``vendor/verible/``. Run ``scripts/fetch_verible.py`` to
    install the pinned release.
    """
    found = find_binary(name)
    if found is None:
        raise VeribleUnavailable(
            f"{name} not found on PATH or in vendor/verible/. "
            "Install it with `uv run python scripts/fetch_verible.py` "
            "or via Homebrew (`brew install verible`)."
        )
    return found


def parse(files: list[Path]) -> ModuleTable:
    """Parse the given SV files into a :class:`ModuleTable`.

    Stub for Phase 1 bootstrap. The CLI plumbing already routes to
    this entry point; the CST walker lands in the same PR as the
    semantic extractor.
    """
    # Probe the binary up-front so callers get the install-hint error
    # rather than a confusing NotImplementedError when verible isn't
    # available. Once parse() lands for real this line stays.
    locate_binary()
    raise NotImplementedError(
        "Verible frontend not yet implemented — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/1"
    )
