from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.pdf_pages import PdfPageSource


def _write_pdf(path: Path, pages: int = 2) -> None:
    images = [Image.new("RGB", (200, 300), "white") for _ in range(pages)]
    images[0].save(path, format="PDF", save_all=True, append_images=images[1:])


def test_pdf_source_reports_renderable_page_count(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    _write_pdf(path, pages=2)

    assert PdfPageSource(path).page_count == 2


def test_pdf_source_renders_page_to_png_bytes(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    _write_pdf(path, pages=1)

    rendered = PdfPageSource(path, dpi=144).render_page(0)

    assert rendered.page_index == 0
    assert rendered.extension == "png"
    assert rendered.width > 0 and rendered.height > 0
    assert rendered.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_pdf_source_rejects_out_of_range_page(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    _write_pdf(path, pages=1)

    with pytest.raises(IndexError):
        PdfPageSource(path).render_page(1)


def test_pdf_source_rejects_non_positive_dpi(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    _write_pdf(path, pages=1)

    with pytest.raises(ValueError, match="DPI"):
        PdfPageSource(path, dpi=0)
