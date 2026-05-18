"""Tests for the byte-offset → (line, column) translator."""

from __future__ import annotations

from rtl_buddy_view._offsets import OffsetIndex


def test_first_line_first_column() -> None:
    idx = OffsetIndex.build("module foo;\nendmodule\n")
    assert idx.line_col(0) == (1, 1)
    assert idx.line_col(6) == (1, 7)  # space after "module"


def test_second_line_after_newline() -> None:
    idx = OffsetIndex.build("module foo;\nendmodule\n")
    # 12 = first byte after the first \n
    assert idx.line_col(12) == (2, 1)


def test_offset_in_middle_of_line() -> None:
    idx = OffsetIndex.build("aaa\nbbb\nccc\n")
    assert idx.line_col(5) == (2, 2)  # second 'b'


def test_negative_offset_clamps_to_origin() -> None:
    idx = OffsetIndex.build("anything\n")
    assert idx.line_col(-5) == (1, 1)


def test_utf8_aware() -> None:
    # The em-dash in the comment is 3 bytes in UTF-8. Verible's
    # offsets are byte-based, so a position after the em-dash should
    # still resolve to the line that contains it.
    src = "// a — b\nmodule x;\nendmodule\n"
    idx = OffsetIndex.build(src)
    # byte index of "module" should be on line 2.
    bytes_until_module = len("// a — b\n".encode("utf-8"))
    assert idx.line_col(bytes_until_module) == (2, 1)
