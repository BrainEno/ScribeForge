from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import cast


class RuntimeToolMissing(RuntimeError):
    pass


ReleaseFetcher = Callable[[], dict[str, object]]
AssetDownloader = Callable[[str], bytes]

_UV_RELEASE_API = "https://api.github.com/repos/astral-sh/uv/releases/latest"
_MAX_UV_DOWNLOAD_BYTES = 100 * 1024 * 1024
_MAX_UV_BINARY_BYTES = 50 * 1024 * 1024


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
    release_fetcher: ReleaseFetcher | None = None,
    asset_downloader: AssetDownloader | None = None,
) -> str:
    """Resolve uv or download its official platform archive into private storage.

    The GitHub release API's published SHA-256 digest is required and verified
    before the executable is written. No installer script is executed and no
    global Python or package environment is modified.
    """
    try:
        return resolve_uv_executable(tools_dir, os_name, which)
    except RuntimeToolMissing:
        pass

    asset_name = _asset_name(os_name, architecture)
    fetch_release = _fetch_latest_release if release_fetcher is None else release_fetcher
    download = _download_bytes if asset_downloader is None else asset_downloader
    release = fetch_release()
    tag = release.get("tag_name")
    assets = release.get("assets")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", tag):
        raise RuntimeToolMissing("the official uv release returned an invalid version tag")
    if not isinstance(assets, list):
        raise RuntimeToolMissing("the official uv release did not provide an asset list")

    asset: dict[str, object] | None = next(
        (
            cast(dict[str, object], item)
            for item in assets
            if isinstance(item, dict) and item.get("name") == asset_name
        ),
        None,
    )
    if asset is None:
        raise RuntimeToolMissing(
            f"the official uv release has no asset for this platform ({asset_name})"
        )
    digest = asset.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise RuntimeToolMissing(
            "the official uv release did not provide a SHA-256 digest; "
            "refusing to install an unverified bootstrap binary"
        )

    url = f"https://github.com/astral-sh/uv/releases/download/{tag}/{asset_name}"
    archive = download(url)
    expected_digest = digest.removeprefix("sha256:").lower()
    actual_digest = hashlib.sha256(archive).hexdigest()
    if actual_digest != expected_digest:
        raise RuntimeToolMissing("the downloaded uv archive failed SHA-256 verification")

    executable_name = "uv.exe" if os_name == "windows" else "uv"
    executable = _extract_uv_binary(archive, asset_name, executable_name)
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


def _fetch_latest_release() -> dict[str, object]:
    request = urllib.request.Request(
        _UV_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ScribeForge-runtime-bootstrap",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except (OSError, ValueError) as exc:
        raise RuntimeToolMissing(f"could not query the official uv release: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeToolMissing("the official uv release API returned invalid metadata")
    return cast(dict[str, object], payload)


def _download_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "ScribeForge-runtime-bootstrap"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_UV_DOWNLOAD_BYTES:
                raise RuntimeToolMissing("the uv bootstrap archive exceeds the size limit")
            data = response.read(_MAX_UV_DOWNLOAD_BYTES + 1)
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
                candidates = [
                    item
                    for item in package.infolist()
                    if PurePosixPath(item.filename).name == executable_name
                    and not item.is_dir()
                    and item.file_size <= _MAX_UV_BINARY_BYTES
                    and not PurePosixPath(item.filename).is_absolute()
                    and ".." not in PurePosixPath(item.filename).parts
                ]
                if not candidates:
                    raise RuntimeToolMissing("the verified uv archive contains no executable")
                if len(candidates) != 1:
                    raise RuntimeToolMissing("the verified uv archive contains ambiguous executables")
                return package.read(candidates[0])
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
            candidates = [
                item
                for item in package.getmembers()
                if PurePosixPath(item.name).name == executable_name
                and item.isfile()
                and item.size <= _MAX_UV_BINARY_BYTES
                and not PurePosixPath(item.name).is_absolute()
                and ".." not in PurePosixPath(item.name).parts
            ]
            if not candidates:
                raise RuntimeToolMissing("the verified uv archive contains no executable")
            if len(candidates) != 1:
                raise RuntimeToolMissing("the verified uv archive contains ambiguous executables")
            extracted = package.extractfile(candidates[0])
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
