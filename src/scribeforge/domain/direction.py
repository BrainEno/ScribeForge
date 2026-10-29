"""Page direction evidence.

Which axis a page reads along decides everything downstream: which coordinate
pairs two lines, what "the next line" means, how a review crop is cut, and how
paragraphs are joined on export. Ancient Chinese, Japanese and Korean books are
commonly set in columns read top-to-bottom and right-to-left, so treating every
page as horizontal is not a loss of accuracy — it is a semantic error.

Two sources of evidence, in this order:

1. **What the engine declared.** Worker contract v2 carries ``direction``; a real
   engine that detects vertical columns is more trustworthy than any heuristic.
2. **What the geometry shows.** Many engines never report a direction (the whole
   v1 contract has no such field), so lines are inspected directly: vertical text
   produces tall, narrow line boxes stacked in columns.

The default remains ``horizontal-lr``. A page is only called vertical when the
evidence is strong, because guessing "vertical" on a horizontal page scrambles
the reading order just as badly in the other direction.
"""

from __future__ import annotations

from scribeforge.domain.ocr import OCRLine, PageDirection, PageOCRResult

# A vertical column is much taller than it is wide. Body characters are roughly
# square, so a run of them in a column produces an aspect ratio far above 1.
_VERTICAL_ASPECT_RATIO = 1.6

# Share of text lines that must look vertical before the page is called vertical.
# A stray tall box (a figure caption, a stamp) must not flip the whole page.
_VERTICAL_LINE_SHARE = 0.6

# Below this many lines there is not enough evidence to override the default.
_MIN_LINES_FOR_DETECTION = 3


def _aspect(line: OCRLine) -> float:
    if line.box.width <= 0:
        return float("inf")
    return line.box.height / line.box.width


def _text_lines(result: PageOCRResult) -> tuple[OCRLine, ...]:
    indexes = result.text_line_indexes
    if not indexes:
        return result.lines
    return tuple(result.lines[index] for index in indexes)


def detect_page_direction(result: PageOCRResult) -> PageDirection | None:
    """Infer the reading direction from line geometry, or ``None`` if unclear.

    ``None`` means "no opinion": the caller should keep whatever direction was
    declared elsewhere rather than treat silence as horizontal.
    """

    lines = _text_lines(result)
    if len(lines) < _MIN_LINES_FOR_DETECTION:
        return None
    vertical = sum(1 for line in lines if _aspect(line) >= _VERTICAL_ASPECT_RATIO)
    share = vertical / len(lines)
    if share < _VERTICAL_LINE_SHARE:
        return None

    # Column order: when the engine already reports columns right-to-left, its
    # line order runs from the right edge towards the left. Detecting that keeps
    # the reading order honest for the rarer left-to-right vertical layouts.
    centres = [
        line.box.x + line.box.width / 2
        for line in lines
        if line.box.height >= line.box.width
    ]
    if len(centres) >= 2 and centres[0] > centres[-1]:
        return PageDirection.VERTICAL_RL
    if len(centres) >= 2:
        return PageDirection.VERTICAL_LR
    return PageDirection.VERTICAL_RL


def resolve_page_direction(
    primary: PageOCRResult,
    secondary: PageOCRResult,
) -> PageDirection:
    """Decide the direction a pair of engine results should be aligned in.

    Rules, in order:

    1. Agreed non-default declarations win outright.
    2. A single vertical declaration wins over the default: horizontal is the
       absence of evidence, vertical is positive evidence, so a lone vertical
       claim is never a fallback value.
    3. Otherwise fall back to geometry, then to the horizontal default.
    """

    declared = [
        result
        for result in (primary, secondary)
        if result.direction_declared
    ]
    vertical = [result.direction for result in declared if result.direction.is_vertical]
    if vertical:
        # Two engines may both see vertical columns but disagree about which way
        # they run. Prefer the primary engine's answer rather than silently
        # reordering a page one engine read the other way round.
        return primary.direction if primary.direction in vertical else vertical[0]

    if declared:
        # Every engine that said anything said horizontal. That is a claim, and it
        # outranks the tall-box heuristic: an engine looked at the page, we did not.
        return declared[0].direction

    detected = detect_page_direction(primary) or detect_page_direction(secondary)
    if detected is not None:
        return detected
    return PageDirection.HORIZONTAL_LR
