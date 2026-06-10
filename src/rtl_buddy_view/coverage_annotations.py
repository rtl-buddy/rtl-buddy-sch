"""Loader + per-node aggregator for the coverage overlay (Phase 6 — #20).

Consumes the LCOV ``.info`` artefacts produced by rtl_buddy's
Verilator coverage flow (``rb -M cov regression --coverage-merge
--coverage-coverview``). Three input shapes are accepted, matching
where that flow leaves data on disk:

- a single combined ``.info`` file (``cov_dir/coverage_merged.info``)
  — ``DA:`` records feed the *lines* channel, ``BRDA:`` records the
  *branches* channel;
- a directory of typed per-dataset files as packed for Coverview
  (``coverage_line_<ds>.info``, ``coverage_branch_<ds>.info``,
  ``coverage_toggle_<ds>.info``, ``coverage_expression_<ds>.info``)
  — the filename picks the channel; expression files are ignored
  (not one of the three v1 channels);
- a ``coverview_regression.zip`` archive — same classification as
  the directory case, read in place without unpacking.

Channel semantics follow the producer side (see
``rtl_buddy/tools/coverview.py`` + Antmicro's ``info-process``):
line and toggle units are ``DA:<line>,<count>`` records (toggle
``.info`` files come from ``verilator_coverage --write-info`` over a
toggle-only database, so their DA lines are toggle points keyed to
the declaration line); branch units are ``BRDA:<line>,<block>,<name>,
<taken>`` with ``-`` meaning never taken. Branch-classified files
also carry echo ``DA:`` lines (info-process keeps DA entries for
lines that have BRDA); those are *not* counted as line units.
Merging across files/datasets is by unit identity — a unit is
covered when any dataset covered it.

The join onto the hierarchy is by **source range, not instance
path** (the producer's LCOV knows files and lines, never elaborated
instances): :meth:`CoverageMap.rollup` takes a node's defining
module's ``(file, start_line, end_line)`` and sums the units whose
line falls inside. All instances of one module therefore share a
rollup — inherent to merged LCOV, and the useful semantic for a
heatmap ("how covered is this module's code").

LCOV ``SF:`` paths are project-root-relative after rtl_buddy's
rewriting pass, while view nodes carry absolute paths — the
file-level join is component-wise *suffix* matching (the SF path's
components must be the trailing components of the node's path, or
vice versa for absolute SF entries against relative node paths).
Ambiguous matches (two SF entries with equally long overlaps)
resolve to "no data" rather than guessing.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote

SCHEMA_VERSION = "1.0"

#: Default Coverview dev-server address used for per-node deep
#: links; override via ``--coverage-url-base``.
DEFAULT_URL_BASE = "http://localhost:5173/"

#: The three v1 channels, in render order. ``expression`` coverage
#: exists on the producer side but is deliberately not a channel
#: here — out of scope per #20.
CHANNELS = ("lines", "branches", "toggles")


class CoverageAnnotationsError(ValueError):
    """Raised when a coverage payload can't be loaded.

    Distinct from the other overlay loader errors so the CLI can
    qualify the failing artefact when several ``--overlay`` flags
    are supplied.
    """


# Channel a typed Coverview file feeds, keyed by basename prefix.
# ``None`` = recognized but ignored (expression). Files matching no
# prefix are treated as combined LCOV (DA → lines, BRDA → branches).
_TYPED_PREFIXES: tuple[tuple[str, str | None], ...] = (
    ("coverage_line_", "lines"),
    ("coverage_branch_", "branches"),
    ("coverage_toggle_", "toggles"),
    ("coverage_expression_", None),
)


@dataclass
class _FileCoverage:
    """Per-SF-path unit→covered maps, one dict per channel.

    Unit keys carry the line number first so range queries can
    filter without knowing the channel: ``lines`` / ``toggles`` use
    the bare ``int`` line, ``branches`` uses ``(line, block, name)``.
    """

    lines: dict[int, bool] = field(default_factory=dict)
    branches: dict[tuple[int, int, str], bool] = field(default_factory=dict)
    toggles: dict[int, bool] = field(default_factory=dict)


@dataclass
class CoverageMap:
    """Parsed + merged LCOV data, queryable by module source range.

    ``url_base`` is mutable on purpose: the overlay protocol's
    ``load(path)`` has no channel for CLI knobs, so ``cli.main``
    assigns the ``--coverage-url-base`` value after load (same
    post-load configuration pattern the TB clock map merge uses).
    """

    source: str
    url_base: str = DEFAULT_URL_BASE
    _files: dict[str, _FileCoverage] = field(default_factory=dict)
    # Memo for the suffix-match resolution; node files repeat across
    # instances so the match is computed once per distinct path.
    _match_cache: dict[str, str | None] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self._files

    def rollup(
        self,
        file: str,
        start_line: int | None,
        end_line: int | None,
    ) -> dict | None:
        """Aggregate coverage for the module spanning ``file``
        ``start_line..end_line`` (inclusive; ``None`` bounds are
        open-ended).

        Returns the ``node.overlays.coverage`` block — per-channel
        ``{covered, total, pct}`` plus a ``coverview_link`` — or
        ``None`` when no LCOV unit of any channel lands in range
        (the renderer's "no coverage data" / gray case).
        """
        sf = self._match_file(file)
        if sf is None:
            return None
        record = self._files[sf]
        block: dict[str, object] = {}
        for channel in CHANNELS:
            units: dict = getattr(record, channel)
            covered = 0
            total = 0
            for key, hit in units.items():
                line = key if isinstance(key, int) else key[0]
                if start_line is not None and line < start_line:
                    continue
                if end_line is not None and line > end_line:
                    continue
                total += 1
                covered += 1 if hit else 0
            if total:
                block[channel] = {
                    "covered": covered,
                    "total": total,
                    "pct": round(100.0 * covered / total, 1),
                }
        if not block:
            return None
        block["coverview_link"] = self._link(sf, start_line)
        return block

    def _link(self, sf: str, start_line: int | None) -> str:
        base = self.url_base if self.url_base.endswith("/") else self.url_base + "/"
        link = f"{base}#/file/{quote(sf, safe='/')}"
        if start_line is not None:
            link += f"?line={start_line}"
        return link

    def _match_file(self, file: str) -> str | None:
        """Resolve a node's source path to a recorded ``SF:`` path.

        Exact match wins; otherwise the SF entry whose components
        form the longest suffix of the node path (or vice versa).
        A tie between distinct entries is ambiguous → ``None``,
        deterministically, rather than tinting from the wrong file.
        """
        if file in self._match_cache:
            return self._match_cache[file]
        match = self._compute_match(file)
        self._match_cache[file] = match
        return match

    def _compute_match(self, file: str) -> str | None:
        if file in self._files:
            return file
        node_parts = _path_parts(file)
        best: str | None = None
        best_len = 0
        tied = False
        for sf in self._files:
            sf_parts = _path_parts(sf)
            short, long_ = sorted((sf_parts, node_parts), key=len)
            if not short or long_[-len(short) :] != short:
                continue
            if len(short) > best_len:
                best, best_len, tied = sf, len(short), False
            elif len(short) == best_len:
                tied = True
        return None if tied else best

    # -- ingestion (module-internal; loaders below populate via these) --

    def _file_record(self, sf: str) -> _FileCoverage:
        return self._files.setdefault(sf, _FileCoverage())


def _path_parts(path: str) -> tuple[str, ...]:
    """Component tuple for suffix matching, ``.``/empty parts dropped.

    LCOV SF paths are posix after rtl_buddy's rewrite; node paths
    are OS-native. PurePosixPath handles both on the platforms we
    support (macOS/Linux).
    """
    return tuple(p for p in PurePosixPath(path).parts if p not in (".", "/"))


def load_coverage_map(path: Path) -> CoverageMap:
    """Load ``path`` (combined ``.info`` file, Coverview-typed
    directory, or ``coverview_regression.zip``) into a
    :class:`CoverageMap`.

    Raises :class:`CoverageAnnotationsError` on unreadable input,
    malformed LCOV records, or a directory/zip with no ``.info``
    members.
    """
    cmap = CoverageMap(source=str(path))
    if path.is_dir():
        members = sorted(path.glob("*.info"))
        if not members:
            raise CoverageAnnotationsError(f"no .info files found in directory: {path}")
        for member in members:
            _ingest_named(cmap, member.name, _read_text(member))
    elif path.suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                names = sorted(n for n in zf.namelist() if n.endswith(".info"))
                if not names:
                    raise CoverageAnnotationsError(
                        f"no .info members found in archive: {path}"
                    )
                for name in names:
                    text = zf.read(name).decode("utf-8", errors="replace")
                    _ingest_named(cmap, PurePosixPath(name).name, text)
        except zipfile.BadZipFile as e:
            raise CoverageAnnotationsError(f"not a zip archive: {path} ({e})") from e
    else:
        _ingest_named(cmap, path.name, _read_text(path))
    return cmap


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CoverageAnnotationsError(f"cannot read {path}: {e}") from e


def _ingest_named(cmap: CoverageMap, basename: str, text: str) -> None:
    """Route one ``.info`` payload into channels based on its name."""
    for prefix, channel in _TYPED_PREFIXES:
        if basename.startswith(prefix):
            if channel is None:
                return  # expression coverage: recognized, out of scope
            da_channel = channel if channel in ("lines", "toggles") else None
            brda_channel = channel if channel == "branches" else None
            _ingest_lcov(cmap, basename, text, da_channel, brda_channel)
            return
    # Unrecognized name → combined LCOV (e.g. coverage_merged.info).
    _ingest_lcov(cmap, basename, text, "lines", "branches")


def _ingest_lcov(
    cmap: CoverageMap,
    origin: str,
    text: str,
    da_channel: str | None,
    brda_channel: str | None,
) -> None:
    """Parse LCOV ``text``, merging units into ``cmap``.

    Only ``SF:`` / ``DA:`` / ``BRDA:`` / ``end_of_record`` matter;
    every other record type (``TN:``, ``LF/LH``, ``BRF/BRH``,
    ``FN*``, …) is summary/metadata we recompute, so it's skipped.
    """
    record: _FileCoverage | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SF:"):
            sf = line[3:].strip()
            if not sf:
                raise CoverageAnnotationsError(f"{origin}:{lineno}: empty SF: record")
            record = cmap._file_record(sf)
        elif line == "end_of_record":
            record = None
        elif line.startswith("DA:"):
            if da_channel is None:
                continue  # echo DA lines in branch-typed files
            if record is None:
                raise CoverageAnnotationsError(
                    f"{origin}:{lineno}: DA record outside SF scope"
                )
            line_no, hit = _split_da(origin, lineno, line[3:])
            units: dict[int, bool] = getattr(record, da_channel)
            units[line_no] = units.get(line_no, False) or hit > 0
        elif line.startswith("BRDA:"):
            if brda_channel is None or record is None:
                if brda_channel is not None and record is None:
                    raise CoverageAnnotationsError(
                        f"{origin}:{lineno}: BRDA record outside SF scope"
                    )
                continue
            key, taken = _split_brda(origin, lineno, line[5:])
            branches = record.branches
            branches[key] = branches.get(key, False) or taken > 0


def _split_da(origin: str, lineno: int, payload: str) -> tuple[int, int]:
    # ``DA:<line>,<count>[,<checksum>]`` — checksum (genhtml extension)
    # tolerated and ignored.
    parts = payload.split(",")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        raise CoverageAnnotationsError(
            f"{origin}:{lineno}: malformed DA record: DA:{payload}"
        ) from None


def _split_brda(
    origin: str, lineno: int, payload: str
) -> tuple[tuple[int, int, str], int]:
    # ``BRDA:<line>,<block>,<name>,<taken>`` — ``-`` means the branch
    # was never evaluated → 0, same as info-process's split_brda.
    parts = payload.split(",", 3)
    try:
        line_no, block, name, taken_raw = (
            int(parts[0]),
            int(parts[1]),
            parts[2],
            parts[3],
        )
        taken = 0 if taken_raw == "-" else int(taken_raw)
    except (IndexError, ValueError):
        raise CoverageAnnotationsError(
            f"{origin}:{lineno}: malformed BRDA record: BRDA:{payload}"
        ) from None
    return (line_no, block, name), taken
