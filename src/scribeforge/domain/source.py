from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

SUPPORTED_EXTENSIONS = frozenset({"png", "jpg"})


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One immutable raster page extracted from a source document."""

    page_index: int
    width: int
    height: int
    image_bytes: bytes
    extension: str

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page index must be zero-based and non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")
        if not self.image_bytes:
            raise ValueError("rendered page image bytes are required")
        if self.extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported page image extension: {self.extension}")


class PageSource(Protocol):
    """Read-only source of raster pages, independent of the concrete file format."""

    @property
    def page_count(self) -> int: ...

    def render_page(self, page_index: int) -> RenderedPage: ...


def page_asset_name(page_index: int, extension: str) -> str:
    if page_index < 0:
        raise ValueError("page index must be zero-based and non-negative")
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported page image extension: {extension}")
    return f"page-{page_index:06d}.{extension}"
