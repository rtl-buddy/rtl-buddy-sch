"""Byte-offset → (line, column) translation.

Verible's CST nodes carry byte offsets (``start``/``end``); we want
1-indexed line and column for :class:`SourceLocation`. This helper
precomputes the line-start offset table once per source file so each
translation is an O(log n) bisect rather than an O(n) scan.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class OffsetIndex:
    """Per-file index of newline offsets.

    ``line_starts[i]`` is the byte offset at which line ``i+1``
    begins. Line numbers are 1-indexed; column numbers are 1-indexed.
    """

    line_starts: tuple[int, ...]

    @classmethod
    def build(cls, text: str) -> "OffsetIndex":
        # Encode once so the offsets match Verible's byte-position
        # semantics on multi-byte characters. ASCII-only files
        # round-trip identically, but anyone using UTF-8 in their
        # SystemVerilog (rare but legal in comments) will appreciate
        # the correctness.
        starts = [0]
        for i, b in enumerate(text.encode("utf-8")):
            if b == ord(b"\n"):
                starts.append(i + 1)
        return cls(line_starts=tuple(starts))

    def line_col(self, byte_offset: int) -> tuple[int, int]:
        """Return ``(line, column)`` (both 1-indexed) for a byte offset."""
        if byte_offset < 0:
            byte_offset = 0
        # bisect_right gives us the index of the first line-start
        # strictly greater than byte_offset; subtract 1 to get the
        # line whose start is <= byte_offset.
        line_idx = bisect_right(self.line_starts, byte_offset) - 1
        line = line_idx + 1
        col = byte_offset - self.line_starts[line_idx] + 1
        return (line, col)
