"""Reading order as its own layer.

Reading order used to be implicit: the pairing function happened to emit pairs in
reading order, and exporters assumed one OCR line equals one paragraph. Both
assumptions are horizontal-only. This module makes the order and the
line-to-paragraph splice explicit decisions driven by ``PageDirection``, so
alignment stays about *pairing* and export stays about *rendering*.

Two rules matter here and are easy to get wrong:

1. **Vertical text is read right-to-left, column by column.** The first reported
   line of a vertical page is the rightmost column, which is also the column a
   reader starts with. Sorting vertical columns by X ascending silently reverses
   the book.
2. **Joined vertical columns must not receive an inserted space.** In vertical
   typesetting a paragraph runs down one column and continues at the top of the
   next; inserting a space there corrupts Latin words and numbers split across
   columns, and Chinese needs no separator at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from scribeforge.domain.ocr import OCRLine, PageDirection
from scribeforge.domain.paragraphs import TextLine, assemble_paragraphs


def reading_order_key(line: OCRLine, direction: PageDirection) -> tuple[float, float]:
    """Sort key placing a line where a reader of this page reaches it.

    Horizontal: top to bottom, then left to right (right to left for RL).
    Vertical right-to-left: rightmost column first, then top to bottom.
    Vertical left-to-right: leftmost column first, then top to bottom.

    A stable sort keeps the engine's own order for genuine ties, so an engine
    that already returns reading order is left untouched.
    """

    center_x = line.box.x + line.box.width / 2
    center_y = line.box.y + line.box.height / 2
    if direction is PageDirection.VERTICAL_RL:
        return (-center_x, line.box.y)
    if direction is PageDirection.VERTICAL_LR:
        return (center_x, line.box.y)
    if direction is PageDirection.HORIZONTAL_RL:
        return (center_y, -center_x)
    return (center_y, center_x)


def order_indexes(lines: Sequence[OCRLine], direction: PageDirection) -> tuple[int, ...]:
    """Indexes of ``lines`` in reading order (stable for ties)."""

    return tuple(
        sorted(range(len(lines)), key=lambda index: reading_order_key(lines[index], direction))
    )


def splice_paragraphs(
    lines: Sequence[str], direction: PageDirection
) -> tuple[str, ...]:
    """Turn reading-order lines into the paragraphs an export should render.

    Text-only convenience wrapper around
    :func:`scribeforge.domain.paragraphs.assemble_paragraphs`. Export should call
    that function directly with geometry and block roles; this one exists for
    callers that hold nothing but strings (and for the vertical case, where the
    printed column break carries no paragraph information anyway).
    """

    return tuple(
        paragraph.text
        for paragraph in assemble_paragraphs(
            [TextLine(text) for text in lines], direction
        )
    )
