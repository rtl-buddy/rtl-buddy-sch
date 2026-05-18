"""Content-hashed cache for the Verible JSON CST.

Re-running ``verible-verilog-syntax --export_json --printtree`` on
every parse is expensive — the binary takes 50-200ms per file even
on a small design, and the AI workflow keeps re-parsing the same
files. We trade a few ms of stat + read for that overhead by
content-hashing each file and memoizing the JSON output under
``<cache-root>/<verible-version>/<sha256>.json``.

Cache layout:

    <cache-root>/
      <verible-version>/        # e.g. "v0.0-4053-g89d4d98a"
        <sha256-of-file>.json   # the verible CST as JSON

Bumping Verible naturally invalidates everything by switching the
versioned subdirectory; users can also nuke the entire cache root
with ``rm -rf`` without breaking anything.

Cache root resolution:

1. ``RTL_BUDDY_VIEW_CACHE_DIR`` (explicit override)
2. ``$XDG_CACHE_HOME/rtl-buddy-view/cst`` (per XDG spec)
3. ``~/.cache/rtl-buddy-view/cst`` (XDG default)

Set ``RTL_BUDDY_VIEW_NO_CACHE=1`` to disable caching entirely — every
parse goes straight to the binary. Useful for measuring real
Verible overhead and for CI runs that shouldn't share state across
jobs.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

_VERIBLE_VERSION_BY_BINARY: dict[str, str] = {}


def cache_root() -> Path:
    """Resolve the cache-root directory per the env-var precedence above."""
    override = os.environ.get("RTL_BUDDY_VIEW_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "rtl-buddy-view" / "cst"
    return Path.home() / ".cache" / "rtl-buddy-view" / "cst"


def is_disabled() -> bool:
    return os.environ.get("RTL_BUDDY_VIEW_NO_CACHE") == "1"


def verible_version(binary: Path) -> str:
    """Run ``binary --version``, memoize, and return a short version tag.

    Memoized in-process so we don't pay the subprocess cost per file.
    The result is consumed only as a cache-directory name; if the
    binary doesn't print a recognisable version we fall back to the
    binary's basename to keep cache directories well-formed.
    """
    key = str(binary)
    cached = _VERIBLE_VERSION_BY_BINARY.get(key)
    if cached is not None:
        return cached
    version = _query_version(binary)
    _VERIBLE_VERSION_BY_BINARY[key] = version
    return version


def _query_version(binary: Path) -> str:
    try:
        proc = subprocess.run(
            [str(binary), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return binary.name
    for line in proc.stdout.splitlines():
        # ``verible-verilog-syntax --version`` prints lines like
        # ``Version\tv0.0-4053-g89d4d98a``. We tolerate either tab or
        # whitespace separation; some Verible builds use plain spaces.
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == "version":
            return parts[1]
    return binary.name


def content_hash(path: Path) -> str:
    """SHA256 of the file's content, hex digest.

    Hashing 1MiB at a time keeps memory bounded on big synthesis
    netlists; for typical RTL files the loop runs exactly once.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_or_compute(
    binary: Path,
    path: Path,
    *,
    compute: Callable[[Path, Path], dict],
) -> dict:
    """Return the cached JSON CST for ``path``, computing on miss.

    ``compute(binary, path)`` is invoked only when the cache lookup
    misses (or when caching is disabled). Cache writes are
    rename-atomic — partial files from a crashed process never poison
    the cache. JSON load errors on read transparently fall through
    to a recompute.
    """
    if is_disabled():
        return compute(binary, path)
    cache_path = _cache_path_for(binary, path)
    if cache_path.exists():
        try:
            with cache_path.open("r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            # Corrupt entry — fall through to a recompute and the
            # subsequent atomic-write will overwrite it.
            pass
    result = compute(binary, path)
    _atomic_write_json(cache_path, result)
    return result


def _cache_path_for(binary: Path, path: Path) -> Path:
    version = verible_version(binary)
    digest = content_hash(path)
    return cache_root() / version / f"{digest}.json"


def _atomic_write_json(dest: Path, payload: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("w") as f:
        json.dump(payload, f)
    tmp.replace(dest)
