"""Tests for OSC-8 hyperlink emission in the tree renderer (Phase 4 — #17).

OSC-8 is a terminal escape that wraps a text run in a clickable
``rtlbuddy://`` URI without changing the visible glyphs. Terminals
that don't speak OSC-8 silently strip the escape so plain-text
piping stays intact.

The Phase 4 JSON renderer already emits ``link`` per node;
extending the tree renderer means terminal users can ctrl/cmd-click
their way into the hub from a plain ASCII rendering.
"""

from __future__ import annotations

import io
from pathlib import Path

from rtl_buddy_view.extractor import Instance, SourceLocation
from rtl_buddy_view.graph import HierNode
from rtl_buddy_view.render import tree as tree_render


# Stand-in for ``sys.stdout`` when running in a real terminal — needs
# both ``write`` and a truthy ``isatty()`` so the auto-detect branch
# fires. StringIO doesn't have ``isatty`` so we wrap it.
class _TtyBuf(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover - exercised below
        return True


def _node_with_loc(
    path: str,
    *,
    inst_name: str | None = None,
    file: str = "/abs/top.sv",
    line: int = 12,
    col: int = 8,
) -> HierNode:
    inst = (
        Instance(
            name=inst_name,
            module_name="ff",
            param_overrides=(),
            port_connections=(),
            location=SourceLocation(file=file, start_line=line, start_column=col),
        )
        if inst_name is not None
        else None
    )
    return HierNode(
        instance_path=path,
        module_name="ff",
        instance=inst,
        module=None,
        is_blackbox=False,
        children=(),
    )


# --- explicit links=True ----------------------------------------------------


def test_explicit_links_emits_osc8_for_each_node() -> None:
    child = _node_with_loc("top.u_x", inst_name="u_x")
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = io.StringIO()
    tree_render.render(root, buf, links=True)
    text = buf.getvalue()
    # OSC-8 open + close escape around the linked text.
    assert "\x1b]8;;rtlbuddy://open?file=/abs/top.sv&line=12&col=8\x1b\\" in text
    assert "\x1b]8;;\x1b\\" in text


def test_explicit_links_false_emits_plain_output() -> None:
    """The default for redirects + tests: no OSC-8 even on TTY-shaped output."""
    child = _node_with_loc("top.u_x", inst_name="u_x")
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = _TtyBuf()
    tree_render.render(root, buf, links=False)
    assert "\x1b]8" not in buf.getvalue()


# --- auto-detect via isatty -------------------------------------------------


def test_auto_detect_enables_links_on_tty() -> None:
    child = _node_with_loc("top.u_x", inst_name="u_x")
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = _TtyBuf()
    tree_render.render(root, buf)  # links=None → auto
    assert "\x1b]8;;rtlbuddy://" in buf.getvalue()


def test_auto_detect_disables_links_on_stringio() -> None:
    """Plain StringIO has no ``isatty``; auto-detect leaves output clean."""
    child = _node_with_loc("top.u_x", inst_name="u_x")
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = io.StringIO()
    tree_render.render(root, buf)
    assert "\x1b" not in buf.getvalue()


def test_no_color_env_var_disables_links(monkeypatch) -> None:
    """``NO_COLOR=1`` vetoes OSC-8 even on a real TTY — matches the
    https://no-color.org convention that some users wire up globally."""
    monkeypatch.setenv("NO_COLOR", "1")
    child = _node_with_loc("top.u_x", inst_name="u_x")
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = _TtyBuf()
    tree_render.render(root, buf)
    assert "\x1b]8" not in buf.getvalue()


# --- nodes without source anchors -------------------------------------------


def test_node_without_location_renders_unlinked() -> None:
    """Synthetic / blackbox nodes have no source anchor; no link emitted."""
    # No instance.location set.
    child = HierNode(
        instance_path="top.u_x",
        module_name="ff",
        instance=Instance(
            name="u_x",
            module_name="ff",
            param_overrides=(),
            port_connections=(),
            location=None,
        ),
        module=None,
        is_blackbox=False,
        children=(),
    )
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = io.StringIO()
    tree_render.render(root, buf, links=True)
    # Even with links=True, the missing anchor means no OSC-8 wrapping.
    assert "\x1b]8" not in buf.getvalue()


# --- visible glyphs are preserved -------------------------------------------


def test_linked_output_still_contains_all_instance_text() -> None:
    """Stripping the escape sequence reproduces the un-linked output."""
    import re

    child = _node_with_loc("top.u_x", inst_name="u_x")
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf_linked = io.StringIO()
    buf_plain = io.StringIO()
    tree_render.render(root, buf_linked, links=True)
    tree_render.render(root, buf_plain, links=False)
    # Strip every ``\x1b]8;;…\x1b\\`` sequence; the visible payload
    # must match the plain output byte-for-byte.
    stripped = re.sub(r"\x1b\]8;;[^\x1b]*?\x1b\\", "", buf_linked.getvalue())
    assert stripped == buf_plain.getvalue()


# --- absolute-path encoding -------------------------------------------------


def test_link_uri_encodes_spaces_in_path() -> None:
    child = _node_with_loc(
        "top.u_x",
        inst_name="u_x",
        file=str(Path("/abs/path with space/top.sv")),
    )
    root = HierNode(
        instance_path="top",
        module_name="top",
        instance=None,
        module=None,
        is_blackbox=False,
        children=(child,),
    )
    buf = io.StringIO()
    tree_render.render(root, buf, links=True)
    text = buf.getvalue()
    assert "/abs/path%20with%20space/top.sv" in text
