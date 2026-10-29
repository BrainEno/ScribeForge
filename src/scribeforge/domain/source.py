from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

SUPPORTED_EXTENSIONS = frozenset({"png", "jpg"})


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One immutable raster page extracted from a source document."""

    page_index: int
    width: int
    height: int
    image_bytes: bytes
    extension: str
    #: How these bytes came to exist: DPI, renderer name and version, source
    #: format. Stored with the page so a later audit can explain a raster it did
    #: not produce, rather than only noticing that it changed.
    render_parameters: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page index must be zero-based and non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("page dimensions must be positive")
        if not self.image_bytes:
            raise ValueError("rendered page image bytes are required")
        if self.extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported page image extension: {self.extension}")
        # Freeze the mapping so a caller cannot edit the provenance of a page it
        # has already handed to the importer.
        object.__setattr__(
            self, "render_parameters", MappingProxyType(dict(self.render_parameters))
        )


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
