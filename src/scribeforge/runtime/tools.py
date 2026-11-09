from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast


class RuntimeToolMissing(RuntimeError):
    pass


AssetDownloader = Callable[[str], bytes]

_UV_VERSION = "0.13.7"
_UV_ARCHIVE_SHA256 = {
    "uv-x86_64-pc-windows-msvc.zip": "0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021",
    "uv-aarch64-pc-windows-msvc.zip": "505d9e65e7692bac3ddf98c66754aa26c5050c409c184af30a881a0a72cc85e3",
    "uv-x86_64-apple-darwin.tar.gz": "20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7",
    "uv-aarch64-apple-darwin.tar.gz": "81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731",
    "uv-x86_64-unknown-linux-gnu.tar.gz": "c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83",
    "uv-aarch64-unknown-linux-gnu.tar.gz": "71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc",
}
_MAX_UV_DOWNLOAD_BYTES = 100 * 1024 * 1024
_MAX_UV_BINARY_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UvAssetLock:
    version: str
    asset_name: str
    sha256: str


def locked_uv_asset(os_name: str, architecture: str) -> UvAssetLock:
    """Return the reviewed uv identity; never follow a mutable latest release."""

    asset_name = _asset_name(os_name, architecture)
    return UvAssetLock(_UV_VERSION, asset_name, _UV_ARCHIVE_SHA256[asset_name])


def resolve_uv_executable(
    tools_dir: Path,
    os_name: str,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Resolve a bundled private uv first, then a developer's existing uv."""
    executable_name = "uv.exe" if os_name == "windows" else "uv"
    bundled = tools_dir / executable_name
    if bundled.is_file():
        return str(bundled)
    system = which("uv")
    if system:
        return system
    raise RuntimeToolMissing(
        "uv bootstrap tool is unavailable; use ensure_uv_executable to fetch "
        "the official release into the private runtime directory"
    )


def ensure_uv_executable(
    tools_dir: Path,
    os_name: str,
    architecture: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    release_lock: UvAssetLock | None = None,
    asset_downloader: AssetDownloader | None = None,
) -> str:
    """Resolve uv or install the reviewed, hash-locked official archive.

    The expected version and digest live in source control, independently of
    the download response. No installer script is executed and no global Python
    or package environment is modified.
    """
    try:
        return resolve_uv_executable(tools_dir, os_name, which)
    except RuntimeToolMissing:
        pass

    expected_asset = _asset_name(os_name, architecture)
    lock = locked_uv_asset(os_name, architecture) if release_lock is None else release_lock
    if lock.asset_name != expected_asset:
        raise RuntimeToolMissing("the uv lock does not match this operating system")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", lock.version):
        raise RuntimeToolMissing("the uv lock contains an invalid version tag")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", lock.sha256):
        raise RuntimeToolMissing("the uv lock contains an invalid SHA-256 digest")

    download = _download_bytes if asset_downloader is None else asset_downloader
    url = f"https://github.com/astral-sh/uv/releases/download/{lock.version}/{lock.asset_name}"
    archive = download(url)
    expected_digest = lock.sha256.lower()
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeToolMissing("the downloaded uv archive failed SHA-256 verification")

    executable_name = "uv.exe" if os_name == "windows" else "uv"
    executable = _extract_uv_binary(archive, lock.asset_name, executable_name)
    tools_dir.mkdir(parents=True, exist_ok=True)
    target = tools_dir / executable_name
    temporary = tools_dir / f"{executable_name}.download"
    try:
        temporary.write_bytes(executable)
        if os.name != "nt":
            temporary.chmod(0o755)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


def _asset_name(os_name: str, architecture: str) -> str:
    os_key = os_name.lower()
    arch_key = architecture.lower()
    if arch_key in {"amd64", "x64", "x86_64"}:
        arch = "x86_64"
    elif arch_key in {"arm64", "aarch64"}:
        arch = "aarch64"
    else:
        raise RuntimeToolMissing(f"uv bootstrap does not support architecture {architecture!r}")

    if os_key == "windows":
        return f"uv-{arch}-pc-windows-msvc.zip"
    if os_key in {"macos", "darwin"}:
        return f"uv-{arch}-apple-darwin.tar.gz"
    if os_key == "linux":
        return f"uv-{arch}-unknown-linux-gnu.tar.gz"
    raise RuntimeToolMissing(f"uv bootstrap does not support operating system {os_name!r}")


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ScribeForge-runtime-bootstrap"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_UV_DOWNLOAD_BYTES:
                raise RuntimeToolMissing("the uv bootstrap archive exceeds the size limit")
            data = cast(bytes, response.read(_MAX_UV_DOWNLOAD_BYTES + 1))
    except RuntimeToolMissing:
        raise
    except (OSError, ValueError) as exc:
        raise RuntimeToolMissing(f"could not download the official uv archive: {exc}") from exc
    if len(data) > _MAX_UV_DOWNLOAD_BYTES:
        raise RuntimeToolMissing("the uv bootstrap archive exceeds the size limit")
    return data


def _extract_uv_binary(archive: bytes, asset_name: str, executable_name: str) -> bytes:
    try:
        if asset_name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                zip_candidates = [
                    item
                    for item in package.infolist()
                    if PurePosixPath(item.filename).name == executable_name
                    and not item.is_dir()
                    and item.file_size <= _MAX_UV_BINARY_BYTES
                    and not PurePosixPath(item.filename).is_absolute()
                    and ".." not in PurePosixPath(item.filename).parts
                ]
                if not zip_candidates:
                    raise RuntimeToolMissing("the verified uv archive contains no executable")
                if len(zip_candidates) != 1:
                    raise RuntimeToolMissing(
                        "the verified uv archive contains ambiguous executables"
                    )
                return package.read(zip_candidates[0])
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
            tar_candidates = [
                item
                for item in package.getmembers()
                if PurePosixPath(item.name).name == executable_name
                and item.isfile()
                and item.size <= _MAX_UV_BINARY_BYTES
                and not PurePosixPath(item.name).is_absolute()
                and ".." not in PurePosixPath(item.name).parts
            ]
            if not tar_candidates:
                raise RuntimeToolMissing("the verified uv archive contains no executable")
            if len(tar_candidates) != 1:
                raise RuntimeToolMissing("the verified uv archive contains ambiguous executables")
            extracted = package.extractfile(tar_candidates[0])
            if extracted is None:
                raise RuntimeToolMissing("could not read the uv executable from its archive")
            with extracted:
                binary = extracted.read(_MAX_UV_BINARY_BYTES + 1)
            if len(binary) > _MAX_UV_BINARY_BYTES:
                raise RuntimeToolMissing("the uv executable exceeds the size limit")
            return binary
    except RuntimeToolMissing:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
        raise RuntimeToolMissing(f"could not unpack the verified uv archive: {exc}") from exc
