"""Tests for the content-hashed CST cache.

These are hermetic — no Verible subprocess. The ``compute`` callback
is a counter that returns synthetic JSON, so we can prove the cache
hits and misses are happening when expected. Cache root is pinned to
``tmp_path`` per test via ``RTL_BUDDY_CACHE_DIR`` so nothing escapes
to the user's real ``~/.cache``.

Covers the public surface ratified in view#109: ``cst_cache.cache_root``
with ``override`` argument; ``cst_cache.get_or_compute`` with
``cache_dir`` keyword; legacy ``_cst_cache`` shim emits
``DeprecationWarning``; legacy env / XDG fallback paths honoured.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any, Callable

import pytest

from rtl_buddy_view import cst_cache


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect cache to ``tmp_path`` and clear the version memo each test."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("RTL_BUDDY_CACHE_DIR", str(cache_dir))
    # Clear both old and new "no-cache" env knobs so cache hits work.
    monkeypatch.delenv("RTL_BUDDY_NO_CACHE", raising=False)
    monkeypatch.delenv("RTL_BUDDY_VIEW_NO_CACHE", raising=False)
    monkeypatch.delenv("RTL_BUDDY_VIEW_CACHE_DIR", raising=False)
    cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    cst_cache._DEPRECATIONS_EMITTED.clear()
    return cache_dir


@pytest.fixture
def fake_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A pretend Verible path. ``verible_version`` is monkeypatched
    so we don't actually spawn anything."""
    monkeypatch.setattr(cst_cache, "_query_version", lambda b: "v0.0-test")
    return tmp_path / "bin" / "verible-verilog-syntax"


def _counted() -> tuple[Callable[[Path, Path], dict], dict[str, int]]:
    state: dict[str, int] = {"calls": 0}

    def compute(_binary: Path, path: Path) -> dict[str, Any]:
        state["calls"] += 1
        return {"path": str(path), "calls_seen": state["calls"]}

    return compute, state


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


# --- core hit/miss behaviour -------------------------------------------------


def test_compute_runs_on_first_call(fake_binary: Path, tmp_path: Path) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    result = cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    assert result["path"].endswith("a.sv")
    assert state["calls"] == 1


def test_second_call_hits_the_cache(fake_binary: Path, tmp_path: Path) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    assert state["calls"] == 1


def test_content_change_invalidates(fake_binary: Path, tmp_path: Path) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    src.write_text("module a;\n  wire x;\nendmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    assert state["calls"] == 2


def test_different_verible_version_invalidates(
    fake_binary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    # Swap the version returned by the binary; the cache key changes.
    cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    monkeypatch.setattr(cst_cache, "_query_version", lambda b: "v0.0-other")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    assert state["calls"] == 2


def test_disabled_via_env(
    fake_binary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RTL_BUDDY_NO_CACHE", "1")
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    assert state["calls"] == 2


def test_disabled_via_legacy_env_warns(
    fake_binary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RTL_BUDDY_VIEW_NO_CACHE", "1")
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    assert state["calls"] == 1
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("RTL_BUDDY_VIEW_NO_CACHE" in str(w.message) for w in deprecations)


# --- robustness ---------------------------------------------------------------


def test_corrupt_cache_falls_through_to_recompute(
    fake_binary: Path, tmp_path: Path
) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    # Corrupt the cache entry.
    digest = cst_cache.content_hash(src)
    cache_file = cst_cache.cache_root() / "v0.0-test" / f"{digest}.json"
    cache_file.write_text("not valid json {")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    # Should recompute and overwrite — but call count must increase.
    assert state["calls"] == 2
    # And the cache file is now valid JSON again.
    assert json.loads(cache_file.read_text())["calls_seen"] == 2


# --- cache_root resolution ladder --------------------------------------------


def test_cache_root_override_argument_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The caller-injected ``override`` outranks every env and XDG path."""
    env_target = tmp_path / "from-env"
    monkeypatch.setenv("RTL_BUDDY_CACHE_DIR", str(env_target))
    override = tmp_path / "from-caller"
    assert cst_cache.cache_root(override) == override.resolve()


def test_cache_root_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("RTL_BUDDY_CACHE_DIR", str(target))
    assert cst_cache.cache_root() == target.resolve()


def test_cache_root_legacy_env_warns_and_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RTL_BUDDY_CACHE_DIR", raising=False)
    legacy = tmp_path / "legacy-from-env"
    monkeypatch.setenv("RTL_BUDDY_VIEW_CACHE_DIR", str(legacy))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = cst_cache.cache_root()
    assert result == legacy.resolve()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("RTL_BUDDY_VIEW_CACHE_DIR" in str(w.message) for w in deprecations)


def test_cache_root_new_env_outranks_legacy_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    new = tmp_path / "new"
    legacy = tmp_path / "legacy"
    monkeypatch.setenv("RTL_BUDDY_CACHE_DIR", str(new))
    monkeypatch.setenv("RTL_BUDDY_VIEW_CACHE_DIR", str(legacy))
    assert cst_cache.cache_root() == new.resolve()


def test_cache_root_xdg_new_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RTL_BUDDY_CACHE_DIR", raising=False)
    monkeypatch.delenv("RTL_BUDDY_VIEW_CACHE_DIR", raising=False)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    # New path doesn't exist yet and legacy doesn't exist either,
    # so we get the new default path (which is what new entries write to).
    assert cst_cache.cache_root() == (xdg / "rtl-buddy" / "sv-cst").resolve()


def test_cache_root_falls_back_to_legacy_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If only the legacy XDG path exists, fall back with a DeprecationWarning."""
    monkeypatch.delenv("RTL_BUDDY_CACHE_DIR", raising=False)
    monkeypatch.delenv("RTL_BUDDY_VIEW_CACHE_DIR", raising=False)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    legacy = xdg / "rtl-buddy-view" / "cst"
    legacy.mkdir(parents=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = cst_cache.cache_root()
    assert result == legacy.resolve()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("legacy location" in str(w.message) for w in deprecations)


def test_cache_root_prefers_new_dir_when_both_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once the new path is populated, the legacy path is ignored — no warning."""
    monkeypatch.delenv("RTL_BUDDY_CACHE_DIR", raising=False)
    monkeypatch.delenv("RTL_BUDDY_VIEW_CACHE_DIR", raising=False)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    new = xdg / "rtl-buddy" / "sv-cst"
    new.mkdir(parents=True)
    legacy = xdg / "rtl-buddy-view" / "cst"
    legacy.mkdir(parents=True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = cst_cache.cache_root()
    assert result == new.resolve()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not deprecations


# --- cache_dir parameter ------------------------------------------------------


def test_get_or_compute_honours_cache_dir(
    fake_binary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cache_dir`` overrides env/XDG when threaded through ``get_or_compute``."""
    # Point env at a directory that *shouldn't* receive the write.
    env_dir = tmp_path / "env-cache"
    monkeypatch.setenv("RTL_BUDDY_CACHE_DIR", str(env_dir))
    injected_dir = tmp_path / "injected-cache"
    compute, _ = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(
        src, verible_binary=fake_binary, compute=compute, cache_dir=injected_dir
    )
    digest = cst_cache.content_hash(src)
    assert (injected_dir / "v0.0-test" / f"{digest}.json").exists()
    # And the env-targeted dir is untouched.
    assert not env_dir.exists()


def test_get_or_compute_default_cache_dir_uses_env(
    fake_binary: Path, tmp_path: Path
) -> None:
    """When ``cache_dir=None`` we fall back to env (set by the autouse fixture)."""
    compute, _ = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    cst_cache.get_or_compute(src, verible_binary=fake_binary, compute=compute)
    # _isolate_cache sets RTL_BUDDY_CACHE_DIR to tmp_path/cache.
    digest = cst_cache.content_hash(src)
    assert (tmp_path / "cache" / "v0.0-test" / f"{digest}.json").exists()


# --- legacy underscore-prefix shim --------------------------------------------


def test_underscore_module_imports_with_warning() -> None:
    """``from rtl_buddy_view import _cst_cache`` should warn but still work."""
    import importlib
    import sys

    # Force a fresh import so the module-level warn fires within the
    # catch_warnings block.
    sys.modules.pop("rtl_buddy_view._cst_cache", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        legacy = importlib.import_module("rtl_buddy_view._cst_cache")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("_cst_cache is deprecated" in str(w.message) for w in deprecations)
    # Symbols still resolve via the legacy module path.
    assert legacy.get_or_compute is cst_cache.get_or_compute
    assert legacy.cache_root is cst_cache.cache_root


# --- version probing (unchanged behaviour, kept for regression) ---------------


def test_version_query_fallback_to_basename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the real subprocess fails, ``verible_version`` returns the binary basename."""
    binary = tmp_path / "weird" / "tool"
    monkeypatch.setattr(cst_cache, "_query_version", lambda b: b.name)
    cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    assert cst_cache.verible_version(binary) == "tool"


def test_version_query_memoizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def fake(_binary: Path) -> str:
        calls["n"] += 1
        return "v0.0-once"

    monkeypatch.setattr(cst_cache, "_query_version", fake)
    cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    binary = tmp_path / "vb"
    cst_cache.verible_version(binary)
    cst_cache.verible_version(binary)
    assert calls["n"] == 1
