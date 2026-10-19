from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.image_pages import ImagePageSource


def _write_image(path: Path, size: tuple[int, int] = (40, 25), image_format: str = "PNG") -> None:
    Image.new("RGB", size, "white").save(path, format=image_format)


def test_image_source_preserves_original_bytes_as_single_page(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    _write_image(path, (40, 25), "PNG")

    source = ImagePageSource(path)

    assert source.page_count == 1
    rendered = source.render_page(0)
    assert (rendered.page_index, rendered.width, rendered.height) == (0, 40, 25)
    assert rendered.extension == "png"
    assert rendered.image_bytes == path.read_bytes()


def test_image_source_reads_jpeg_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "page.jpg"
    _write_image(path, (60, 30), "JPEG")

    rendered = ImagePageSource(path).render_page(0)

    assert (rendered.width, rendered.height, rendered.extension) == (60, 30, "jpg")


def test_image_source_rejects_out_of_range_page(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    _write_image(path)

    with pytest.raises(IndexError):
        ImagePageSource(path).render_page(1)


def test_image_source_rejects_unsupported_format(tmp_path: Path) -> None:
    path = tmp_path / "page.bmp"
    Image.new("RGB", (10, 10), "white").save(path, format="BMP")

    with pytest.raises(ValueError, match="unsupported image format"):
        ImagePageSource(path)
