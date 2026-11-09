from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from scribeforge.runtime.tools import (
    RuntimeToolMissing,
    UvAssetLock,
    ensure_uv_executable,
    locked_uv_asset,
    resolve_uv_executable,
)


def test_release_prefers_bundled_uv_over_system_path(tmp_path: Path) -> None:
    bundled = tmp_path / "uv.exe"
    bundled.write_bytes(b"binary")

    resolved = resolve_uv_executable(tmp_path, "windows", lambda _: "C:/system/uv.exe")

    assert resolved == str(bundled)


def test_development_can_fall_back_to_uv_on_path(tmp_path: Path) -> None:
    resolved = resolve_uv_executable(tmp_path, "linux", lambda _: "/usr/bin/uv")

    assert resolved == "/usr/bin/uv"


def test_missing_uv_has_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeToolMissing, match="uv bootstrap tool"):
        resolve_uv_executable(tmp_path, "windows", lambda _: None)


def _uv_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("uv-x86_64-pc-windows-msvc/uv.exe", b"verified uv binary")
    return buffer.getvalue()


def _lock(archive: bytes, *, digest: str | None = None) -> UvAssetLock:
    return UvAssetLock(
        version="0.13.4",
        asset_name="uv-x86_64-pc-windows-msvc.zip",
        sha256=digest or hashlib.sha256(archive).hexdigest(),
    )


def test_missing_uv_downloads_locked_archive_after_verifying_digest(tmp_path: Path) -> None:
    archive = _uv_archive()
    requested: list[str] = []

    def download(url: str) -> bytes:
        requested.append(url)
        return archive

    executable = ensure_uv_executable(
        tmp_path / "tools",
        "windows",
        "AMD64",
        which=lambda _: None,
        release_lock=_lock(archive),
        asset_downloader=download,
    )

    assert Path(executable).read_bytes() == b"verified uv binary"
    expected_url = (
        "https://github.com/astral-sh/uv/releases/download/0.13.4/uv-x86_64-pc-windows-msvc.zip"
    )
    assert requested == [expected_url]


def test_missing_uv_refuses_archive_with_wrong_digest(tmp_path: Path) -> None:
    archive = _uv_archive()

    with pytest.raises(RuntimeToolMissing, match="SHA-256 verification"):
        ensure_uv_executable(
            tmp_path / "tools",
            "windows",
            "x86_64",
            which=lambda _: None,
            release_lock=_lock(archive, digest="0" * 64),
            asset_downloader=lambda _: archive,
        )

    assert not (tmp_path / "tools" / "uv.exe").exists()


def test_missing_uv_rejects_invalid_lock_before_download(tmp_path: Path) -> None:
    archive = _uv_archive()

    def unexpected(_url: str) -> bytes:
        raise AssertionError("an invalid lock must not trigger a download")

    with pytest.raises(RuntimeToolMissing, match="invalid SHA-256"):
        ensure_uv_executable(
            tmp_path,
            "windows",
            "x86_64",
            which=lambda _: None,
            release_lock=_lock(archive, digest="not-a-digest"),
            asset_downloader=unexpected,
        )


def test_missing_uv_rejects_unsupported_architecture(tmp_path: Path) -> None:
    with pytest.raises(RuntimeToolMissing, match="does not support architecture"):
        ensure_uv_executable(tmp_path, "windows", "sparc", which=lambda _: None)


@pytest.mark.parametrize(
    ("os_name", "architecture", "asset_name", "sha256"),
    [
        (
            "windows",
            "AMD64",
            "uv-x86_64-pc-windows-msvc.zip",
            "0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021",
        ),
        (
            "windows",
            "arm64",
            "uv-aarch64-pc-windows-msvc.zip",
            "505d9e65e7692bac3ddf98c66754aa26c5050c409c184af30a881a0a72cc85e3",
        ),
        (
            "macos",
            "x86_64",
            "uv-x86_64-apple-darwin.tar.gz",
            "20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7",
        ),
        (
            "darwin",
            "aarch64",
            "uv-aarch64-apple-darwin.tar.gz",
            "81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731",
        ),
        (
            "linux",
            "x86_64",
            "uv-x86_64-unknown-linux-gnu.tar.gz",
            "c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83",
        ),
        (
            "linux",
            "arm64",
            "uv-aarch64-unknown-linux-gnu.tar.gz",
            "71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc",
        ),
    ],
)
def test_committed_uv_0_13_7_locks_cover_supported_platforms(
    os_name: str, architecture: str, asset_name: str, sha256: str
) -> None:
    lock = locked_uv_asset(os_name, architecture)

    assert lock.version == "0.13.7"
    assert lock.asset_name == asset_name
    assert lock.sha256 == sha256
