from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pypdfium2 as pdfium


@dataclass(frozen=True, slots=True)
class RenderedPdfPage:
    page_index: int
    path: Path
    width: int
    height: int
    sha256: str


class PDFiumRasterizer:
    def page_count(self, source: Path) -> int:
        document = pdfium.PdfDocument(source)
        try:
            return len(document)
        finally:
            document.close()

    def render_page(
        self,
        source: Path,
        page_index: int,
        destination: Path,
        *,
        dpi: int = 144,
    ) -> RenderedPdfPage:
        if dpi <= 0:
            raise ValueError("PDF render dpi must be positive")

        document = pdfium.PdfDocument(source)
        try:
            page_count = len(document)
            if not 0 <= page_index < page_count:
                raise IndexError(
                    f"PDF page index {page_index} is outside 0..{page_count - 1}"
                )
            page = document[page_index]
            try:
                bitmap = page.render(scale=dpi / 72.0)
                try:
                    image = bitmap.to_pil()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(f".{destination.name}.tmp")
                    try:
                        image.save(temporary, format="PNG", optimize=False, compress_level=6)
                        width, height = image.size
                        temporary.replace(destination)
                    finally:
                        image.close()
                        temporary.unlink(missing_ok=True)
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()

        digest = sha256(destination.read_bytes()).hexdigest()
        return RenderedPdfPage(
            page_index=page_index,
            path=destination,
            width=width,
            height=height,
            sha256=digest,
        )
