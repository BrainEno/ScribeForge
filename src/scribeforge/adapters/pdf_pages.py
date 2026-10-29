from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium

from scribeforge.domain.source import RenderedPage

DEFAULT_DPI = 300


class PdfPageSource:
    """Rasterizes PDF pages to PNG through pdfium, one immutable page at a time."""

    def __init__(self, path: Path, *, dpi: int = DEFAULT_DPI) -> None:
        if dpi <= 0:
            raise ValueError("PDF render DPI must be positive")
        self._path = Path(path)
        self._dpi = dpi
        self._scale = dpi / 72.0
        document = pdfium.PdfDocument(str(self._path))
        try:
            self._page_count = len(document)
        finally:
            document.close()
        if self._page_count < 1:
            raise ValueError("PDF source contains no pages")

    @property
    def page_count(self) -> int:
        return self._page_count

    def render_page(self, page_index: int) -> RenderedPage:
        if not 0 <= page_index < self._page_count:
            raise IndexError(f"PDF page index out of range: {page_index}")
        document = pdfium.PdfDocument(str(self._path))
        try:
            page = document[page_index]
            try:
                bitmap = page.render(scale=self._scale)
                image = bitmap.to_pil()
            finally:
                page.close()
        finally:
            document.close()

        try:
            width, height = image.size
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        finally:
            image.close()
        return RenderedPage(
            page_index=page_index,
            width=int(width),
            height=int(height),
            image_bytes=buffer.getvalue(),
            extension="png",
            render_parameters={
                "dpi": self._dpi,
                "renderer": "pdfium",
                "source_format": "pdf",
            },
        )
