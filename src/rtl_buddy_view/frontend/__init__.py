"""Pluggable parser frontend.

Two backends, selected by name:

- ``verible`` (default) — source-faithful concrete syntax tree.
  Comments, parameter overrides, and source positions are preserved.
  Cannot resolve generate blocks or parameterized instantiations on
  its own; the graph builder falls back to ``slang`` for those.
- ``slang`` — elaborated AST via pyslang. Phase 2 fallback (currently
  raises ``NotImplementedError``). Use for designs that rely heavily
  on generates or parameter-driven instantiation.

The frontend layer is the *only* part of the package that subprocesses
the Verible binary or imports ``pyslang``. Everything downstream of
:func:`parse_to_modules` works on :class:`rtl_buddy_view.extractor.Module`
objects with no toolchain runtime dependency.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from rtl_buddy_view.extractor import ModuleTable


class Frontend(str, Enum):
    verible = "verible"
    slang = "slang"


def parse_to_modules(
    files: list[Path],
    *,
    frontend: Frontend = Frontend.verible,
) -> ModuleTable:
    """Parse SV source into a :class:`ModuleTable`.

    Dispatches to the requested frontend. Phase 1 wires Verible; slang
    is a stub that raises ``NotImplementedError``. See
    [rtl-buddy/rtl-buddy-view#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2)
    for slang activation.
    """
    if frontend is Frontend.verible:
        from rtl_buddy_view.frontend.verible import parse as verible_parse

        return verible_parse(files)
    if frontend is Frontend.slang:
        from rtl_buddy_view.frontend.slang import parse as slang_parse

        return slang_parse(files)
    raise ValueError(f"Unknown frontend: {frontend!r}")
