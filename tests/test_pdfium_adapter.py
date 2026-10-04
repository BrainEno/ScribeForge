from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.pdfium import PDFiumRasterizer


def _pdf(path: Path) -> None:
    first = Image.new("RGB", (80, 60), (240, 240, 240))
    second = Image.new("RGB", (50, 90), (20, 40, 60))
    first.save(
        path,
        format="PDF",
        save_all=True,
        append_images=[second],
        resolution=72.0,
    )
    first.close()
    second.close()


def test_pdfium_reports_page_count_and_renders_stable_png_evidence(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    destination = tmp_path / "pages" / "page-000002.png"
    _pdf(source)
    rasterizer = PDFiumRasterizer()

    assert rasterizer.page_count(source) == 2

    rendered = rasterizer.render_page(source, 1, destination, dpi=72)

    assert rendered.page_index == 1
    assert rendered.path == destination
    assert rendered.width > 0
    assert rendered.height > 0
    assert rendered.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    with Image.open(destination) as image:
        assert image.format == "PNG"
        assert image.size == (rendered.width, rendered.height)


def test_pdfium_render_atomically_replaces_stale_destination(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    destination = tmp_path / "page.png"
    _pdf(source)
    destination.write_bytes(b"stale")

    PDFiumRasterizer().render_page(source, 0, destination, dpi=72)

    assert destination.read_bytes().startswith(b"\x89PNG")
    assert not (tmp_path / ".page.png.tmp").exists()


def test_pdfium_rejects_invalid_dpi_and_page_index(tmp_path: Path) -> None:
    source = tmp_path / "book.pdf"
    _pdf(source)
    rasterizer = PDFiumRasterizer()

    with pytest.raises(ValueError, match="dpi"):
        rasterizer.render_page(source, 0, tmp_path / "page.png", dpi=0)
    with pytest.raises(IndexError, match="page index"):
        rasterizer.render_page(source, 2, tmp_path / "page.png", dpi=72)
