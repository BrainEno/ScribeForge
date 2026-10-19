from __future__ import annotations

from pathlib import Path

from scribeforge.adapters.image_pages import ImagePageSource
from scribeforge.adapters.pdf_pages import PdfPageSource
from scribeforge.domain.source import PageSource

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


def open_page_source(path: Path) -> PageSource:
    """Select a page source for a supported scan/PDF file by its suffix."""

    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix == ".pdf":
        return PdfPageSource(source_path)
    if suffix in _IMAGE_SUFFIXES:
        return ImagePageSource(source_path)
    raise ValueError(f"unsupported source file type: {source_path.suffix}")
