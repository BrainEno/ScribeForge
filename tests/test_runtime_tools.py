from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from scribeforge.runtime.tools import (
    RuntimeToolMissing,
    ensure_uv_executable,
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


def test_missing_uv_downloads_official_archive_after_verifying_digest(tmp_path: Path) -> None:
    archive = _uv_archive()
    requested: list[str] = []

    def fetch_release() -> dict[str, object]:
        return {
            "tag_name": "0.13.4",
            "assets": [
                {
                    "name": "uv-x86_64-pc-windows-msvc.zip",
                    "digest": f"sha256:{hashlib.sha256(archive).hexdigest()}",
                }
            ],
        }

    def download(url: str) -> bytes:
        requested.append(url)
        return archive

    executable = ensure_uv_executable(
        tmp_path / "tools",
        "windows",
        "AMD64",
        which=lambda _: None,
        release_fetcher=fetch_release,
        asset_downloader=download,
    )

    assert Path(executable).read_bytes() == b"verified uv binary"
    assert requested == [
        "https://github.com/astral-sh/uv/releases/download/"
        "0.13.4/uv-x86_64-pc-windows-msvc.zip"
    ]


def test_missing_uv_refuses_archive_with_wrong_digest(tmp_path: Path) -> None:
    archive = _uv_archive()

    def fetch_release() -> dict[str, object]:
        return {
            "tag_name": "0.13.4",
            "assets": [
                {
                    "name": "uv-x86_64-pc-windows-msvc.zip",
                    "digest": f"sha256:{'0' * 64}",
                }
            ],
        }

    with pytest.raises(RuntimeToolMissing, match="SHA-256 verification"):
        ensure_uv_executable(
            tmp_path / "tools",
            "windows",
            "x86_64",
            which=lambda _: None,
            release_fetcher=fetch_release,
            asset_downloader=lambda _: archive,
        )

    assert not (tmp_path / "tools" / "uv.exe").exists()


def test_missing_uv_rejects_release_without_digest(tmp_path: Path) -> None:
    with pytest.raises(RuntimeToolMissing, match="did not provide a SHA-256 digest"):
        ensure_uv_executable(
            tmp_path,
            "windows",
            "x86_64",
            which=lambda _: None,
            release_fetcher=lambda: {
                "tag_name": "0.13.4",
                "assets": [{"name": "uv-x86_64-pc-windows-msvc.zip"}],
            },
            asset_downloader=lambda _: b"anything",
        )


def test_missing_uv_rejects_unsupported_architecture(tmp_path: Path) -> None:
    with pytest.raises(RuntimeToolMissing, match="does not support architecture"):
        ensure_uv_executable(
            tmp_path, "windows", "sparc", which=lambda _: None
        )
