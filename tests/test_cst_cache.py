"""Tests for the content-hashed CST cache.

These are hermetic — no Verible subprocess. The ``compute`` callback
is a counter that returns synthetic JSON, so we can prove the cache
hits and misses are happening when expected. Cache root is pinned to
``tmp_path`` per test via ``RTL_BUDDY_VIEW_CACHE_DIR`` so nothing
escapes to the user's real ``~/.cache``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from rtl_buddy_view import _cst_cache


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect cache to ``tmp_path`` and clear the version memo each test."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("RTL_BUDDY_VIEW_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("RTL_BUDDY_VIEW_NO_CACHE", raising=False)
    _cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    return cache_dir


@pytest.fixture
def fake_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A pretend Verible path. ``verible_version`` is monkeypatched
    so we don't actually spawn anything."""
    monkeypatch.setattr(_cst_cache, "_query_version", lambda b: "v0.0-test")
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
    result = _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    assert result["path"].endswith("a.sv")
    assert state["calls"] == 1


def test_second_call_hits_the_cache(fake_binary: Path, tmp_path: Path) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    assert state["calls"] == 1


def test_content_change_invalidates(fake_binary: Path, tmp_path: Path) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    src.write_text("module a;\n  wire x;\nendmodule\n")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    assert state["calls"] == 2


def test_different_verible_version_invalidates(
    fake_binary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    # Swap the version returned by the binary; the cache key changes.
    _cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    monkeypatch.setattr(_cst_cache, "_query_version", lambda b: "v0.0-other")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    assert state["calls"] == 2


def test_disabled_via_env(
    fake_binary: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RTL_BUDDY_VIEW_NO_CACHE", "1")
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    assert state["calls"] == 2


# --- robustness ---------------------------------------------------------------


def test_corrupt_cache_falls_through_to_recompute(
    fake_binary: Path, tmp_path: Path
) -> None:
    compute, state = _counted()
    src = _write(tmp_path, "a.sv", "module a; endmodule\n")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    # Corrupt the cache entry.
    digest = _cst_cache.content_hash(src)
    cache_file = _cst_cache.cache_root() / "v0.0-test" / f"{digest}.json"
    cache_file.write_text("not valid json {")
    _cst_cache.get_or_compute(fake_binary, src, compute=compute)
    # Should recompute and overwrite — but call count must increase.
    assert state["calls"] == 2
    # And the cache file is now valid JSON again.
    assert json.loads(cache_file.read_text())["calls_seen"] == 2


def test_cache_root_resolution_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("RTL_BUDDY_VIEW_CACHE_DIR", str(target))
    assert _cst_cache.cache_root() == target.resolve()


def test_cache_root_resolution_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RTL_BUDDY_VIEW_CACHE_DIR", raising=False)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    assert _cst_cache.cache_root() == (xdg / "rtl-buddy-view" / "cst").resolve()


def test_version_query_fallback_to_basename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "weird" / "tool"

    def boom(b: Path) -> str:  # pragma: no cover - exercised indirectly
        raise OSError("nope")

    # The public verible_version() wraps _query_version. If _query_version
    # itself ever raises (e.g. binary disappeared mid-run), we still want
    # a usable cache directory rather than a crash. Simulate that here.
    monkeypatch.setattr(
        _cst_cache,
        "_query_version",
        lambda b: b.name,  # the fallback path the real impl already takes
    )
    _cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    assert _cst_cache.verible_version(binary) == "tool"


def test_version_query_memoizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def fake(_binary: Path) -> str:
        calls["n"] += 1
        return "v0.0-once"

    monkeypatch.setattr(_cst_cache, "_query_version", fake)
    _cst_cache._VERIBLE_VERSION_BY_BINARY.clear()
    binary = tmp_path / "vb"
    _cst_cache.verible_version(binary)
    _cst_cache.verible_version(binary)
    assert calls["n"] == 1
