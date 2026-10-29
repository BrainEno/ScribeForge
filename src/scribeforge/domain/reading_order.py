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

#: Sentence-final punctuation. A column that ends with one of these closes a
#: paragraph; anything else continues into the next column.
_TERMINAL_PUNCTUATION = frozenset("。！？；：…」』）】》〉”’!?;:")


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


def _is_vertical(direction: PageDirection) -> bool:
    return direction.is_vertical


def splice_paragraphs(
    lines: Sequence[str], direction: PageDirection
) -> tuple[str, ...]:
    """Turn reading-order lines into the paragraphs an export should render.

    *Horizontal pages are returned unchanged.* A horizontal OCR line is a visual
    line, and joining visual lines into paragraphs needs hyphenation and
    justification evidence that this pipeline does not have yet; guessing would
    silently rewrite the book. Export keeps the accepted line as the unit.
    *Vertical pages are joined.* A vertical OCR line is a column, and a column is
    a fragment of a paragraph; keeping one paragraph per column would insert a
    paragraph break mid-sentence on every page. Columns are joined with no
    separator until one ends with sentence-final punctuation.
    """

    texts = [line.strip() for line in lines]
    if not _is_vertical(direction):
        return tuple(lines)

    paragraphs: list[str] = []
    current: list[str] = []
    for text in texts:
        if not text:
            # A blank line is an explicit break even on a vertical page.
            if current:
                paragraphs.append("".join(current))
                current = []
            continue
        current.append(text)
        if text[-1] in _TERMINAL_PUNCTUATION:
            paragraphs.append("".join(current))
            current = []
    if current:
        paragraphs.append("".join(current))
    return tuple(paragraphs)
