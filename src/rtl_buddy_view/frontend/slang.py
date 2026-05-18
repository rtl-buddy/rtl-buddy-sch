"""slang frontend — Phase 2 fallback for elaborated views.

Reserved for designs where Verible's source-level CST cannot resolve
the hierarchy on its own: generate blocks, parameterized
instantiations, recursive modules. The graph builder is expected to
try the Verible result first and only invoke this frontend for the
specific instances Verible could not resolve.

Phase 2 issue: [rtl-buddy/rtl-buddy-view#2](https://github.com/rtl-buddy/rtl-buddy-view/issues/2).
"""

from __future__ import annotations

from pathlib import Path

from rtl_buddy_view.extractor import ModuleTable


def parse(files: list[Path]) -> ModuleTable:
    raise NotImplementedError(
        "slang fallback frontend is a Phase 2 feature — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/2"
    )
