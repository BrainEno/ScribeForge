from __future__ import annotations

import errno
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from typing_extensions import Self

from scribeforge.runtime import tools
from scribeforge.runtime.tools import RuntimeToolMissing, UvAssetLock, ensure_uv_executable


def _windows_archive(*, include_uv: bool = True, duplicate_uv: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("uv/config.txt", "fixture")
        if include_uv:
            package.writestr("uv-x86_64-pc-windows-msvc/uv.exe", b"verified uv binary")
        if duplicate_uv:
            package.writestr("other/uv.exe", b"ambiguous")
    return buffer.getvalue()


def _windows_lock(archive: bytes) -> UvAssetLock:
    return UvAssetLock(
        "0.13.7",
        "uv-x86_64-pc-windows-msvc.zip",
        hashlib.sha256(archive).hexdigest(),
    )


class _Response:
    def __init__(self, body: bytes, *, length: str | None = None) -> None:
        self.body = body
        self.headers = {} if length is None else {"Content-Length": length}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


def test_uv_network_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> _Response:
        raise OSError("network unavailable")

    monkeypatch.setattr(tools.urllib.request, "urlopen", unavailable)

    with pytest.raises(RuntimeToolMissing, match="network unavailable"):
        tools._download_bytes("https://github.com/astral-sh/uv/releases/download/fixture")


def test_uv_response_size_limit_is_enforced_before_and_after_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "_MAX_UV_DOWNLOAD_BYTES", 16)
    monkeypatch.setattr(
        tools.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(b"not read", length="17"),
    )
    with pytest.raises(RuntimeToolMissing, match="size limit"):
        tools._download_bytes("https://github.com/astral-sh/uv/releases/download/fixture")

    monkeypatch.setattr(
        tools.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(b"x" * 17),
    )
    with pytest.raises(RuntimeToolMissing, match="size limit"):
        tools._download_bytes("https://github.com/astral-sh/uv/releases/download/fixture")


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (b"not a zip archive", "could not unpack"),
        (_windows_archive(include_uv=False), "contains no executable"),
        (_windows_archive(duplicate_uv=True), "ambiguous executables"),
    ],
    ids=["malformed", "missing", "ambiguous"],
)
def test_verified_but_invalid_uv_archive_is_not_installed(
    tmp_path: Path, archive: bytes, message: str
) -> None:
    with pytest.raises(RuntimeToolMissing, match=message):
        ensure_uv_executable(
            tmp_path,
            "windows",
            "x86_64",
            which=lambda _: None,
            release_lock=_windows_lock(archive),
            asset_downloader=lambda _: archive,
        )

    assert not (tmp_path / "uv.exe").exists()


def test_uv_download_returns_body_under_the_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tools.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _Response(b"tiny archive"),
    )
    assert tools._download_bytes("https://example.invalid/uv.zip") == b"tiny archive"


def _linux_tarball(*, include_uv: bool = True, duplicate_uv: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        payload = b"linux uv binary"
        for name in (["bin/uv"] if include_uv else []) + (["alt/uv"] if duplicate_uv else []):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            package.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _linux_lock(archive: bytes) -> UvAssetLock:
    return UvAssetLock(
        "0.13.7",
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        hashlib.sha256(archive).hexdigest(),
    )


@pytest.mark.parametrize(
    ("archive", "message"),
    [
        (_linux_tarball(include_uv=False), "contains no executable"),
        (_linux_tarball(duplicate_uv=True), "ambiguous executables"),
    ],
    ids=["tar-missing", "tar-ambiguous"],
)
def test_verified_but_invalid_linux_tarball_is_not_installed(
    tmp_path: Path, archive: bytes, message: str
) -> None:
    with pytest.raises(RuntimeToolMissing, match=message):
        ensure_uv_executable(
            tmp_path,
            "linux",
            "x86_64",
            which=lambda _: None,
            release_lock=_linux_lock(archive),
            asset_downloader=lambda _: archive,
        )
    assert not (tmp_path / "uv").exists()


def test_locked_tarball_installs_private_uv(tmp_path: Path) -> None:
    binary = b"linux uv binary"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as package:
        info = tarfile.TarInfo("uv-x86_64-unknown-linux-gnu/uv")
        info.size = len(binary)
        package.addfile(info, io.BytesIO(binary))
    archive = buffer.getvalue()
    lock = UvAssetLock(
        "0.13.7",
        "uv-x86_64-unknown-linux-gnu.tar.gz",
        hashlib.sha256(archive).hexdigest(),
    )

    executable = ensure_uv_executable(
        tmp_path,
        "linux",
        "x86_64",
        which=lambda _: None,
        release_lock=lock,
        asset_downloader=lambda _: archive,
    )

    assert Path(executable).read_bytes() == binary


def test_invalid_lock_platform_and_version_fail_before_download(tmp_path: Path) -> None:
    archive = _windows_archive()
    lock = _windows_lock(archive)
    with pytest.raises(RuntimeToolMissing, match="does not match"):
        ensure_uv_executable(
            tmp_path,
            "windows",
            "x86_64",
            which=lambda _: None,
            release_lock=UvAssetLock(lock.version, "other.zip", lock.sha256),
            asset_downloader=lambda _: archive,
        )
    with pytest.raises(RuntimeToolMissing, match="invalid version"):
        ensure_uv_executable(
            tmp_path,
            "windows",
            "x86_64",
            which=lambda _: None,
            release_lock=UvAssetLock("../latest", lock.asset_name, lock.sha256),
            asset_downloader=lambda _: archive,
        )
    with pytest.raises(RuntimeToolMissing, match="operating system"):
        ensure_uv_executable(tmp_path, "plan9", "x86_64", which=lambda _: None)


@pytest.mark.parametrize(
    "failure",
    [
        OSError(errno.ENOSPC, "simulated disk full"),
        PermissionError(errno.EACCES, "simulated permission denied"),
    ],
    ids=["disk-full", "permission-denied"],
)
def test_uv_install_write_failure_cleans_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError
) -> None:
    archive = _windows_archive()
    original_write = Path.write_bytes

    def fail_temporary(self: Path, data: bytes) -> int:
        if self.name == "uv.exe.download":
            raise failure
        return original_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_temporary)
    with pytest.raises(OSError) as caught:
        ensure_uv_executable(
            tmp_path,
            "windows",
            "x86_64",
            which=lambda _: None,
            release_lock=_windows_lock(archive),
            asset_downloader=lambda _: archive,
        )

    assert caught.value is failure
    assert not (tmp_path / "uv.exe").exists()
    assert not (tmp_path / "uv.exe.download").exists()


def test_existing_private_uv_is_not_downloaded_again(tmp_path: Path) -> None:
    bundled = tmp_path / "uv.exe"
    bundled.write_bytes(b"old executable")

    def unexpected(_url: str) -> bytes:
        raise AssertionError("an existing private uv must not be replaced implicitly")

    assert ensure_uv_executable(
        tmp_path,
        "windows",
        "x86_64",
        which=lambda _: None,
        asset_downloader=unexpected,
    ) == str(bundled)
