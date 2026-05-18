"""Tests for the Phase 1 filelist parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from rtl_buddy_view._filelist import FilelistError, parse_filelist


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_parses_one_file_per_line(tmp_path: Path) -> None:
    _write(tmp_path, "a.sv", "module a; endmodule\n")
    _write(tmp_path, "b.sv", "module b; endmodule\n")
    f = _write(tmp_path, "files.f", "a.sv\nb.sv\n")
    out = parse_filelist(f)
    assert {p.name for p in out} == {"a.sv", "b.sv"}


def test_strips_comments_and_blank_lines(tmp_path: Path) -> None:
    _write(tmp_path, "a.sv", "module a; endmodule\n")
    f = _write(
        tmp_path,
        "files.f",
        "# header\n\n  // also comment\na.sv  # trailing\n",
    )
    out = parse_filelist(f)
    assert [p.name for p in out] == ["a.sv"]


def test_rejects_unsupported_directives(tmp_path: Path) -> None:
    f = _write(tmp_path, "files.f", "+incdir+/some/path\na.sv\n")
    with pytest.raises(FilelistError, match="not supported"):
        parse_filelist(f)


def test_rejects_missing_files(tmp_path: Path) -> None:
    f = _write(tmp_path, "files.f", "ghost.sv\n")
    with pytest.raises(FilelistError, match="does not exist"):
        parse_filelist(f)


def test_rejects_empty_filelist(tmp_path: Path) -> None:
    f = _write(tmp_path, "files.f", "# only comments\n")
    with pytest.raises(FilelistError, match="empty"):
        parse_filelist(f)
