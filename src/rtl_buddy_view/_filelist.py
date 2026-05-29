"""Filelist parsing.

Phase 1 supports the trivial subset: one absolute-or-relative file
path per line, blank lines and ``#`` / ``//`` comments ignored.
``+incdir+`` and ``-y`` library directives, ``+define+`` macros, and
``-f`` recursive includes will land alongside the broader extractor —
they're listed here so future work has a clear target but raise
``FilelistError`` if encountered today rather than silently being
treated as filenames.
"""

from __future__ import annotations

from pathlib import Path


class FilelistError(ValueError):
    pass


_UNSUPPORTED_PREFIXES = ("+incdir+", "+define+", "-y", "-f")


def parse_filelist(path: Path) -> list[Path]:
    text = path.read_text()
    files: list[Path] = []
    base = path.parent
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith(_UNSUPPORTED_PREFIXES):
            raise FilelistError(
                f"{path}:{lineno}: directive {line.split()[0]!r} is not "
                f"supported in Phase 1; see issue #1 for the planned "
                f"filelist surface."
            )
        candidate = (base / line).resolve()
        if candidate.is_dir():
            # A directory entry is an include dir, not a source file —
            # e.g. a ``+incdir+`` path that an upstream generator (rb
            # hier's filelist strip) reduced to a bare path. The Verible
            # frontend parses each source standalone and never consumes
            # include dirs, so skip it rather than letting ``read_text``
            # blow up with IsADirectoryError downstream.
            continue
        if not candidate.exists():
            raise FilelistError(f"{path}:{lineno}: file does not exist: {candidate}")
        files.append(candidate)
    if not files:
        raise FilelistError(f"{path}: filelist is empty")
    return files
