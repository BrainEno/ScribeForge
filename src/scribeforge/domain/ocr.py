from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


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


class PageDirection(str, Enum):
    """Reading direction of the text on a page.

    Ancient Chinese, Japanese and Korean books are commonly set in columns read
    top-to-bottom and right-to-left. Line geometry, alignment, review crops and
    export paragraph joining all depend on this, so it is page-level evidence
    rather than a rendering preference.
    """

    HORIZONTAL_LR = "horizontal-lr"
    HORIZONTAL_RL = "horizontal-rl"
    VERTICAL_RL = "vertical-rl"
    VERTICAL_LR = "vertical-lr"

    @property
    def is_vertical(self) -> bool:
        return self in (PageDirection.VERTICAL_RL, PageDirection.VERTICAL_LR)

    @property
    def columns_flow_right_to_left(self) -> bool:
        return self in (PageDirection.VERTICAL_RL, PageDirection.HORIZONTAL_RL)


class BlockType(str, Enum):
    """Structural role of a detected region.

    Engines report these labels; downstream stages use them to drop page
    furniture, recognise chapter titles, keep figures out of the text layer and
    decide which regions deserve a review crop. ``UNKNOWN`` exists so a new
    engine label never fails a page.
    """

    TEXT = "text"
    TITLE = "title"
    FOOTNOTE = "footnote"
    FIGURE = "figure"
    TABLE = "table"
    HEADER = "header"
    FOOTER = "footer"
    FORMULA = "formula"
    MARGINALIA = "marginalia"
    UNKNOWN = "unknown"

    @property
    def is_page_furniture(self) -> bool:
        return self in (BlockType.HEADER, BlockType.FOOTER)

    @property
    def is_non_text(self) -> bool:
        return self in (BlockType.FIGURE, BlockType.TABLE)


@dataclass(frozen=True, slots=True)
class OCRLine:
    text: str
    box: BoundingBox
    tokens: tuple[OCRToken, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def lowest_confidence(self) -> float | None:
        """Lowest known confidence for this line, from tokens or the line itself.

        Returns ``None`` when the engine reported no confidence at all, which is
        materially different from a low score: absence must not be treated as
        agreement, and it must not be treated as zero either.
        """

        values = [token.confidence for token in self.tokens if token.confidence is not None]
        if self.confidence is not None:
            values.append(self.confidence)
        return min(values) if values else None


@dataclass(frozen=True, slots=True)
class OCRBlock:
    """A structural region covering a contiguous run of lines.

    ``line_end`` is exclusive. Engine contract v2 reports blocks so that block
    labels survive into the domain layer; v1 output simply produces no blocks.
    """

    block_type: BlockType
    box: BoundingBox
    line_start: int = 0
    line_end: int = 0

    def __post_init__(self) -> None:
        if self.line_start < 0:
            raise ValueError("block line_start must be non-negative")
        if self.line_end < self.line_start:
            raise ValueError("block line_end must not precede line_start")

    @property
    def line_count(self) -> int:
        return self.line_end - self.line_start


@dataclass(frozen=True, slots=True)
class PageOCRResult:
    page_index: int
    engine: str
    engine_version: str
    lines: tuple[OCRLine, ...]
    blocks: tuple[OCRBlock, ...] = ()
    direction: PageDirection = PageDirection.HORIZONTAL_LR
    # True only when the engine actually reported a direction. The default value is
    # the absence of evidence, not a claim, and the two must stay distinguishable:
    # otherwise a v1 worker (which cannot report one) looks like an engine that
    # examined the page and pronounced it horizontal.
    direction_declared: bool = False

    def __post_init__(self) -> None:
        if self.page_index < 0:
            raise ValueError("page_index must be zero-based and non-negative")
        if not self.engine.strip():
            raise ValueError("engine name is required")
        for block in self.blocks:
            if block.line_end > len(self.lines):
                raise ValueError("block line range exceeds the reported lines")

    def block_of_line(self, line_index: int) -> OCRBlock | None:
        """Return the block covering ``line_index``, if the engine reported one."""

        for block in self.blocks:
            if block.line_start <= line_index < block.line_end:
                return block
        return None

    @property
    def text_line_indexes(self) -> tuple[int, ...]:
        """Indexes of lines that belong to the readable text layer.

        Page furniture (headers/footers) and non-text regions (figures/tables)
        are excluded. When an engine reports no blocks, every line counts as
        text: absence of block evidence must not silently delete content.
        """

        if not self.blocks:
            return tuple(range(len(self.lines)))
        kept: list[int] = []
        for index in range(len(self.lines)):
            block = self.block_of_line(index)
            if block is None or (
                not block.block_type.is_page_furniture
                and not block.block_type.is_non_text
            ):
                kept.append(index)
        return tuple(kept)


class OCREngine(Protocol):
    @property
    def name(self) -> str: ...

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult: ...


def page_text(result: PageOCRResult) -> str:
    return "\n".join(line.text for line in result.lines)


def text_layer(result: PageOCRResult) -> str:
    """Readable text of a page, excluding page furniture and figures."""

    return "\n".join(result.lines[index].text for index in result.text_line_indexes)


def tokens(result: PageOCRResult) -> Sequence[OCRToken]:
    return tuple(token for line in result.lines for token in line.tokens)


def has_confidence(result: PageOCRResult) -> bool:
    """Whether any line of this result carries a usable confidence value."""

    return any(line.lowest_confidence is not None for line in result.lines)
