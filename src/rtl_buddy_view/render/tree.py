"""ASCII tree renderer.

Output shape::

    top
    ├── u_fifo : fifo
    │   ├── u_wr_ptr : counter
    │   └── u_rd_ptr : counter
    └── u_ctrl : ctrl

The top line shows just the module name (no instance prefix — at the
root we don't have one). Every other line shows
``<instance-name> : <module-name>``. Blackbox modules render with a
trailing ``(blackbox)`` tag on the module-name slot so reviewers can
see at a glance which definitions weren't found.

Designed to be terminal-friendly and easy to feed to an LLM.
"""

from __future__ import annotations

from typing import IO

from rtl_buddy_view.graph import HierNode


def render(node: HierNode, out: IO[str]) -> None:
    """Render ``node`` and its subtree as ASCII to ``out``."""
    out.write(f"{node.module_name}\n")
    _render_children(node.children, prefix="", out=out)


def _render_children(
    children: tuple[HierNode, ...], *, prefix: str, out: IO[str]
) -> None:
    last_idx = len(children) - 1
    for i, child in enumerate(children):
        is_last = i == last_idx
        branch = "└── " if is_last else "├── "
        next_prefix = prefix + ("    " if is_last else "│   ")
        tag = " (blackbox)" if child.is_blackbox else ""
        inst_name = (
            child.instance.name if child.instance is not None else child.module_name
        )
        out.write(f"{prefix}{branch}{inst_name} : {child.module_name}{tag}\n")
        _render_children(child.children, prefix=next_prefix, out=out)
