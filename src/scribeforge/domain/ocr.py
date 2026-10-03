from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError("bounding-box values must be normalized to 0..1")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("bounding box must fit inside the normalized page")


@dataclass(frozen=True, slots=True)
class OCRToken:
    text: str
    confidence: float | None
    box: BoundingBox

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    box: BoundingBox
    tokens: tuple[OCRToken, ...] = ()


@dataclass(frozen=True, slots=True)
class PageOCRResult:
    page_index: int
    engine: str
    engine_version: str
    lines: tuple[OCRLine, ...]

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page_index must be zero-based and non-negative")
        if not self.engine.strip():
            raise ValueError("engine name is required")


class OCREngine(Protocol):
    @property
    def name(self) -> str: ...

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult: ...


def page_text(result: PageOCRResult) -> str:
    return "\n".join(line.text for line in result.lines)


def tokens(result: PageOCRResult) -> Sequence[OCRToken]:
    return tuple(token for line in result.lines for token in line.tokens)
