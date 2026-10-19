from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.image_pages import ImagePageSource
from scribeforge.adapters.pdf_pages import PdfPageSource
from scribeforge.adapters.sources import open_page_source


def test_open_page_source_dispatches_by_suffix(tmp_path: Path) -> None:
    png = tmp_path / "page.png"
    Image.new("RGB", (10, 10), "white").save(png, format="PNG")

    pdf = tmp_path / "book.pdf"
    Image.new("RGB", (200, 300), "white").save(pdf, format="PDF")

    assert isinstance(open_page_source(png), ImagePageSource)
    assert isinstance(open_page_source(pdf), PdfPageSource)


def test_open_page_source_rejects_unsupported_suffix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported source file type"):
        open_page_source(tmp_path / "scan.tiff")
