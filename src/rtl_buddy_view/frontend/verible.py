"""Verible frontend — subprocess wrapper around ``verible-verilog-syntax``.

The full Phase 1 implementation will:

1. Locate the Verible binary (pinned release fetched by the setup
   flow into ``vendor/verible/``, or on ``PATH`` for dev installs).
2. Run ``verible-verilog-syntax --export_json <file>`` per source file.
3. Cache the resulting JSON CST keyed on the file's content hash, so
   reparsing on every query is avoided.
4. Walk the CST via Verible's ``verible_verilog_syntax`` Python helper
   to produce :class:`rtl_buddy_view.extractor.Module` instances.

Phase 1 issue: [rtl-buddy/rtl-buddy-view#1](https://github.com/rtl-buddy/rtl-buddy-view/issues/1).
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.extractor import ModuleTable


def parse(files: list[Path]) -> ModuleTable:
    """Parse the given SV files into a :class:`ModuleTable`.

    Stub for Phase 1 bootstrap. The CLI plumbing already routes to
    this entry point; implementing the body lands in the same PR that
    adds the Verible binary fetch script.
    """
    raise NotImplementedError(
        "Verible frontend not yet implemented — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/1"
    )
