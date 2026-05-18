"""ASCII tree renderer.

Output shape::

    top
    ├── u_fifo : fifo
    │   ├── u_wr_ptr : counter
    │   └── u_rd_ptr : counter
    └── u_ctrl : ctrl

Designed to be terminal-friendly and easy to feed to an LLM. Blackbox
modules render with a ``(blackbox)`` suffix on the module-name slot.
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.graph import HierNode


def render(node: HierNode, out: IO[str]) -> None:
    """Stub for Phase 1. Signature locked; implementation in [#1]."""
    raise NotImplementedError(
        "ASCII tree renderer is a Phase 1 task — see "
        "https://github.com/rtl-buddy/rtl-buddy-view/issues/1"
    )
