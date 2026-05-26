"""Content-hashed cache for the Verible JSON CST. Public API.

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

Cache root resolution (decreasing precedence):

1. ``override`` argument to :func:`cache_root` (caller-injected,
   e.g. from rtl_buddy's ``cfg-cst-cache`` block in
   ``root_config.yaml``).
2. ``RTL_BUDDY_CACHE_DIR`` env var.
3. ``RTL_BUDDY_VIEW_CACHE_DIR`` env var (deprecated alias; emits a
   ``DeprecationWarning`` and will be removed in the next minor).
4. ``$XDG_CACHE_HOME/rtl-buddy/sv-cst`` (per XDG spec).
5. ``~/.cache/rtl-buddy/sv-cst`` (XDG default).

If the new XDG default doesn't exist yet but the legacy path
``<xdg-cache>/rtl-buddy-view/cst`` does, the legacy path is read as
a fallback with a one-time ``DeprecationWarning``. New writes go to
the new path; the legacy path is never written to again.

Set ``RTL_BUDDY_NO_CACHE=1`` to disable caching entirely — every
parse goes straight to the binary. Useful for measuring real
Verible overhead and for CI runs that shouldn't share state across
jobs. ``RTL_BUDDY_VIEW_NO_CACHE`` is honoured as a deprecated alias.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import warnings
from pathlib import Path
from typing import Callable

_VERIBLE_VERSION_BY_BINARY: dict[str, str] = {}

# Track which deprecation warnings we've already emitted so we don't
# spam users on every cache hit. The cache module is loaded once per
# process; that's the right scope for "warn once."
_DEPRECATIONS_EMITTED: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key in _DEPRECATIONS_EMITTED:
        return
    _DEPRECATIONS_EMITTED.add(key)
    warnings.warn(message, DeprecationWarning, stacklevel=3)


def cache_root(override: Path | None = None) -> Path:
    """Resolve the cache-root directory per the precedence ladder above.

    ``override`` is the caller-injected path (typically from rtl_buddy's
    ``cfg-cst-cache.dir`` in ``root_config.yaml``). When ``None``, we
    fall back to env / XDG / home in order. The deprecated
    ``RTL_BUDDY_VIEW_CACHE_DIR`` and legacy ``rtl-buddy-view/cst``
    directory are honoured for one minor version with a
    ``DeprecationWarning``.
    """
    if override is not None:
        return Path(override).expanduser().resolve()
    env_new = os.environ.get("RTL_BUDDY_CACHE_DIR")
    if env_new:
        return Path(env_new).expanduser().resolve()
    env_old = os.environ.get("RTL_BUDDY_VIEW_CACHE_DIR")
    if env_old:
        _warn_once(
            "env_var",
            "RTL_BUDDY_VIEW_CACHE_DIR is deprecated; use "
            "RTL_BUDDY_CACHE_DIR. The old name will be removed in the "
            "next minor release.",
        )
        return Path(env_old).expanduser().resolve()
    new_root = _xdg_default("rtl-buddy", "sv-cst")
    if new_root.exists():
        return new_root
    legacy_root = _xdg_default("rtl-buddy-view", "cst")
    if legacy_root.exists():
        _warn_once(
            "legacy_dir",
            f"Reading CST cache from legacy location {legacy_root}; "
            f"new entries will be written to {new_root}. The legacy "
            "directory will be ignored in the next minor release — "
            "delete it with `rm -rf` once you've migrated.",
        )
        return legacy_root
    return new_root


def _xdg_default(*parts: str) -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return (Path(xdg).expanduser().resolve()).joinpath(*parts)
    return Path.home().joinpath(".cache", *parts)


def is_disabled() -> bool:
    """True if caching is disabled via the env. Both new and legacy names honoured."""
    if os.environ.get("RTL_BUDDY_NO_CACHE") == "1":
        return True
    if os.environ.get("RTL_BUDDY_VIEW_NO_CACHE") == "1":
        _warn_once(
            "no_cache_env",
            "RTL_BUDDY_VIEW_NO_CACHE is deprecated; use RTL_BUDDY_NO_CACHE. "
            "The old name will be removed in the next minor release.",
        )
        return True
    return False


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
    sv_path: Path,
    *,
    verible_binary: Path,
    compute: Callable[[Path, Path], dict],
    cache_dir: Path | None = None,
) -> dict:
    """Return the cached JSON CST for ``sv_path``, computing on miss.

    ``compute(verible_binary, sv_path)`` is invoked only when the cache
    lookup misses (or when caching is disabled). Cache writes are
    rename-atomic — partial files from a crashed process never poison
    the cache. JSON load errors on read transparently fall through to
    a recompute.

    ``cache_dir`` overrides the cache-root resolution (see
    :func:`cache_root`). Defaults to ``None`` to preserve the env / XDG
    fallback behaviour for callers that don't inject a path.
    """
    if is_disabled():
        return compute(verible_binary, sv_path)
    cache_path = _cache_path_for(verible_binary, sv_path, cache_dir)
    if cache_path.exists():
        try:
            with cache_path.open("r") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            # Corrupt entry — fall through to a recompute and the
            # subsequent atomic-write will overwrite it.
            pass
    result = compute(verible_binary, sv_path)
    _atomic_write_json(cache_path, result)
    return result


def _cache_path_for(binary: Path, path: Path, cache_dir: Path | None) -> Path:
    version = verible_version(binary)
    digest = content_hash(path)
    return cache_root(cache_dir) / version / f"{digest}.json"


def _atomic_write_json(dest: Path, payload: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("w") as f:
        json.dump(payload, f)
    tmp.replace(dest)
