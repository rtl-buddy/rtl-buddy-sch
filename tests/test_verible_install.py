"""Tests for the Verible installer.

Hermetic — no network access. The download primitive is monkeypatched
to produce a synthetic tarball matching the upstream Verible layout
(``<inner>/bin/verible-verilog-syntax``), so we exercise the marker,
checksum, and post-extract validation paths without touching GitHub.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from rtl_buddy_view import _verible_install as vi


def _synthetic_tarball(inner_dir_name: str) -> bytes:
    """Build a tar.gz whose layout matches upstream Verible.

    Top-level directory is ``inner_dir_name`` containing ``bin/`` with
    every tool from ``VERIBLE_TOOLS``. The binary content is a shebang
    + ``true`` so the file exists and is non-empty — we don't need it
    to run for these tests.
    """
    buf = io.BytesIO()
    payload = b"#!/bin/sh\nexit 0\n"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for tool in vi.VERIBLE_TOOLS:
            info = tarfile.TarInfo(name=f"{inner_dir_name}/bin/{tool}")
            info.size = len(payload)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


@pytest.fixture(
    params=[
        pytest.param("verible-{version}-testplat", id="suffixed-template"),
        pytest.param("verible-{version}", id="bare-template"),
    ]
)
def fake_download(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> dict:
    """Monkeypatch ``_download`` to write a synthetic tarball.

    Parametrized over both template shapes upstream actually uses
    (macOS-style ``verible-{ver}-<suffix>`` and Linux-style
    ``verible-{ver}``) so the install path is exercised on both.
    """
    template = request.param
    inner_dir_name = template.format(version=vi.VERIBLE_PINNED_VERSION)
    tarball_bytes = _synthetic_tarball(inner_dir_name)
    asset = vi.PlatformAsset(
        asset_suffix="testplat.tar.gz",
        inner_dir_template=template,
        sha256=hashlib.sha256(tarball_bytes).hexdigest(),
    )
    monkeypatch.setattr(vi, "_resolve_asset", lambda: asset)

    calls = {"count": 0}

    def fake_dl(url: str, dest: Path) -> None:
        calls["count"] += 1
        dest.write_bytes(tarball_bytes)

    monkeypatch.setattr(vi, "_download", fake_dl)
    return calls


def test_install_downloads_and_extracts(tmp_path: Path, fake_download: dict) -> None:
    bin_dir = vi.install(target_dir=tmp_path)
    assert bin_dir.is_dir()
    assert (bin_dir / "verible-verilog-syntax").exists()
    assert (tmp_path / vi.VERIBLE_PINNED_VERSION / ".installed").exists()
    assert fake_download["count"] == 1


def test_install_is_idempotent(tmp_path: Path, fake_download: dict) -> None:
    vi.install(target_dir=tmp_path)
    vi.install(target_dir=tmp_path)
    # Second call should skip the download because the marker exists.
    assert fake_download["count"] == 1


def test_install_force_redownloads(tmp_path: Path, fake_download: dict) -> None:
    vi.install(target_dir=tmp_path)
    vi.install(target_dir=tmp_path, force=True)
    assert fake_download["count"] == 2


def test_install_rejects_bad_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bogus_asset = vi.PlatformAsset(
        asset_suffix="testplat.tar.gz",
        inner_dir_template="verible-{version}-testplat",
        sha256="00" * 32,
    )
    tarball_bytes = _synthetic_tarball(
        bogus_asset.inner_dir_template.format(version=vi.VERIBLE_PINNED_VERSION)
    )
    monkeypatch.setattr(vi, "_resolve_asset", lambda: bogus_asset)
    monkeypatch.setattr(
        vi, "_download", lambda url, dest: dest.write_bytes(tarball_bytes)
    )
    with pytest.raises(vi.VeribleInstallError, match="SHA256 mismatch"):
        vi.install(target_dir=tmp_path)
    # Failed install must not leave a marker behind.
    assert not (tmp_path / vi.VERIBLE_PINNED_VERSION / ".installed").exists()


def test_find_binary_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/opt/system/bin/" + name)
    found = vi.find_binary("verible-verilog-syntax")
    assert found == Path("/opt/system/bin/verible-verilog-syntax")


def test_find_binary_falls_back_to_vendor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_download: dict
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    vi.install(target_dir=tmp_path)
    found = vi.find_binary("verible-verilog-syntax", vendor_dir=tmp_path)
    assert found is not None
    assert found.exists()
    assert found.name == "verible-verilog-syntax"


def test_find_binary_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    found = vi.find_binary("verible-verilog-syntax", vendor_dir=tmp_path)
    assert found is None


def test_real_platform_resolution() -> None:
    """The actual platform-resolver should succeed on macOS / linux-x86_64.

    Guards against a typo in ``CHECKSUMS`` that would only surface
    when someone tried to install.
    """
    asset = vi._resolve_asset()
    assert asset.asset_suffix.endswith(".tar.gz")
    assert asset.inner_dir_template
