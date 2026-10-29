from __future__ import annotations

from pathlib import Path

from PIL import Image

from scribeforge.domain.source import RenderedPage

_EXTENSIONS = {"PNG": "png", "JPEG": "jpg"}


class ImagePageSource:
    """Single-image (PNG/JPEG) page source that preserves the original bytes."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        with Image.open(self._path) as image:
            image_format = image.format
            self._width, self._height = image.size
        if image_format is None or image_format not in _EXTENSIONS:
            raise ValueError(f"unsupported image format: {image_format}")
        self._extension = _EXTENSIONS[image_format]

    @property
    def page_count(self) -> int:
        return 1

    def render_page(self, page_index: int) -> RenderedPage:
        if page_index != 0:
            raise IndexError(f"image source has a single page, got index {page_index}")
        return RenderedPage(
            page_index=0,
            width=self._width,
            height=self._height,
            image_bytes=self._path.read_bytes(),
            extension=self._extension,
            render_parameters={
                "dpi": None,
                "renderer": "passthrough",
                "source_format": self._extension.upper(),
            },
        )
