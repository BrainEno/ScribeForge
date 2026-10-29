from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.page_asset import (
    PageAssetError,
    PageAssetEvidence,
    file_sha256,
    reencode_image,
    verify_page_asset,
    verify_recorded_bytes,
)


def test_page_asset_evidence_records_digest_and_size() -> None:
    data = b"some page raster bytes"
    evidence = PageAssetEvidence.from_bytes(data)

    assert evidence.sha256 == hashlib.sha256(data).hexdigest()
    assert evidence.byte_size == len(data)
    assert evidence.version == 1


def test_page_asset_evidence_rejects_bad_digest() -> None:
    with pytest.raises(ValueError, match="digest"):
        PageAssetEvidence(
            sha256="not-hex",
            byte_size=3,
        )


def test_page_asset_evidence_rejects_empty_bytes() -> None:
    with pytest.raises(ValueError, match="carry bytes"):
        PageAssetEvidence.from_bytes(b"")


def test_verify_page_asset_accepts_matching_raster(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"raster bytes")
    evidence = PageAssetEvidence.from_bytes(b"raster bytes")

    actual = verify_page_asset(path, evidence, what="test raster")
    assert actual == evidence.sha256


def test_verify_page_asset_rejects_missing_file(tmp_path: Path) -> None:
    evidence = PageAssetEvidence.from_bytes(b"raster bytes")
    with pytest.raises(PageAssetError, match="missing"):
        verify_page_asset(tmp_path / "nope.png", evidence, what="test raster")


def test_verify_page_asset_rejects_replaced_raster(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"original bytes")
    evidence = PageAssetEvidence.from_bytes(b"original bytes")

    path.write_bytes(b"replacement bytes")
    with pytest.raises(
        PageAssetError, match="not the raster this project recorded"
    ):
        verify_page_asset(path, evidence, what="test raster")


def test_verify_page_asset_without_evidence_returns_digest(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"legacy raster bytes")
    actual = verify_page_asset(path, None, what="legacy raster")
    assert actual == hashlib.sha256(b"legacy raster bytes").hexdigest()


def test_verify_recorded_bytes_accepts_matching() -> None:
    data = b"in-memory raster"
    evidence = PageAssetEvidence.from_bytes(data)
    verify_recorded_bytes(data, evidence, what="in-memory raster")


def test_verify_recorded_bytes_rejects_mismatch() -> None:
    evidence = PageAssetEvidence.from_bytes(b"original")
    with pytest.raises(PageAssetError, match="no longer matches"):
        verify_recorded_bytes(b"replacement", evidence, what="in-memory raster")


def test_file_sha256_streams_content(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    assert file_sha256(path) == hashlib.sha256(b"x" * (2 * 1024 * 1024)).hexdigest()


def _make_png(tmp_path: Path, name: str = "page.png") -> Path:
    from PIL import Image as PILImage

    path = tmp_path / name
    PILImage.new("RGB", (100, 150), "white").save(path, format="PNG")
    return path


def test_reencode_image_png_to_jpeg(tmp_path: Path) -> None:
    source = _make_png(tmp_path)
    original = source.read_bytes()
    data, extension = reencode_image(original, format="jpeg", quality=85)

    assert extension == "jpg"
    assert hashlib.sha256(data).hexdigest() != hashlib.sha256(original).hexdigest()

    # The re-encoded bytes round-trip through Pillow.
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"


def test_reencode_image_png_to_png_keeps_format(tmp_path: Path) -> None:
    source = _make_png(tmp_path)
    original = source.read_bytes()
    data, extension = reencode_image(original, format="png")

    assert extension == "png"
    assert hashlib.sha256(data).hexdigest() != hashlib.sha256(original).hexdigest()


def test_reencode_image_rejects_bad_format() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        reencode_image(b"not an image", format="webp")
