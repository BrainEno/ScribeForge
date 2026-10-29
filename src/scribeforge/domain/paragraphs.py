"""Rebuild paragraphs from OCR lines.

An OCR engine returns *visual* lines: the typesetter's line breaks, not the
author's paragraphs. Exporting one `<p>` per visual line turns every printed line
into a paragraph, so a page of prose arrives as a column of fragments ending
mid-sentence. The six benchmark pages are 36 real paragraphs; re-broken at the
book's measure they are 161 visual lines, which is what the old exporter emitted:
161 `<p>` elements, 14 of them cut through a sentence.

The evidence for a paragraph boundary is the same one a human reader uses:

* **Indentation.** The first line of a paragraph starts further right than the
  body's left margin (Chinese books indent by two characters).
* **A short line.** The last line of a paragraph stops before the right margin,
  because the paragraph ended before the measure did.
* **A gap.** Extra space between lines marks a deliberate separation.
* **Punctuation.** A line ending in 。！？」 closes something; a line ending in ，
  or an ASCII letter has not finished.
* **Block type.** The engine already told us which lines are titles and
  footnotes; those are their own units no matter what the geometry says.

No single rule is trusted on its own. A full-measure line that happens to end in
a full stop is *not* a paragraph end — Chinese typesetters keep going in that
case — so punctuation alone only breaks a line when the geometry agrees (short
line) or is unavailable. Silence about geometry must not produce either extreme:
one paragraph per page or one per line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise

from scribeforge.domain.ocr import BoundingBox, PageDirection

#: Characters that close a sentence, a quoted remark, or a parenthetical.
_TERMINAL_PUNCTUATION = frozenset("。！？；：…!?;:")
#: Closing marks that may follow terminal punctuation and still end a paragraph.
_CLOSING_MARKS = frozenset("”’」』）】》〉〕］\"')]")
#: How many characters of indentation count as "a new paragraph".
INDENT_CHARACTERS = 1.5
#: A line is "short" when it stops at least this many characters before the measure.
SHORT_LINE_CHARACTERS = 1.5
#: A vertical gap this many times the page's typical gap separates paragraphs.
GAP_RATIO = 1.6


class LineRole(str, Enum):
    """What a line is for, as far as export is concerned."""

    BODY = "body"
    HEADING = "heading"
    FOOTNOTE = "footnote"

    @property
    def is_standalone(self) -> bool:
        """A heading or footnote is its own unit: it never joins a neighbour."""

        return self is not LineRole.BODY


@dataclass(frozen=True, slots=True)
class TextLine:
    """One accepted line, with whatever layout evidence survived verification."""

    text: str
    box: BoundingBox | None = None
    role: LineRole = LineRole.BODY

    @property
    def right(self) -> float:
        assert self.box is not None
        return self.box.x + self.box.width

    @property
    def bottom(self) -> float:
        assert self.box is not None
        return self.box.y + self.box.height


@dataclass(frozen=True, slots=True)
class Paragraph:
    """One paragraph as a reader would see it."""

    role: LineRole
    text: str


@dataclass(frozen=True, slots=True)
class PageMetrics:
    """Per-page layout measurements, derived from the lines themselves.

    Public because paragraph reconstruction and cross-page flow have to agree on
    what "short line" and "indented line" mean. Two copies of that rule would drift,
    and the drift shows up as a paragraph joined on one page and split on the next.
    """

    left_margin: float
    measure_right: float
    character_width: float
    line_gap: float

    @property
    def indent_threshold(self) -> float:
        return self.character_width * INDENT_CHARACTERS

    @property
    def short_threshold(self) -> float:
        return self.character_width * SHORT_LINE_CHARACTERS

    @property
    def gap_threshold(self) -> float:
        return self.line_gap * GAP_RATIO

    def is_short(self, line: TextLine) -> bool:
        """Whether ``line`` stops well before the page's right edge."""

        if line.box is None:
            return False
        return (self.measure_right - line.right) > self.short_threshold

    def is_indented(self, line: TextLine) -> bool:
        """Whether ``line`` starts to the right of the body's left margin."""

        if line.box is None:
            return False
        return (line.box.x - self.left_margin) > self.indent_threshold


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def measure_page(lines: Sequence[TextLine]) -> PageMetrics | None:
    """Measure the page, or report that there is nothing to measure."""

    measured = [(line, line.box) for line in lines if line.box is not None]
    boxes = [box for _, box in measured if box is not None]
    if len(boxes) < 2:
        return None

    left_margin = min(box.x for box in boxes)
    measure_right = max(box.x + box.width for box in boxes)
    widths = [
        (box.width / len(line.text))
        for line, box in measured
        if box is not None and len(line.text) > 0 and box.width > 0
    ]
    character_width = _median(widths) if widths else 0.02

    gaps: list[float] = []
    ordered = sorted(measured, key=lambda pair: (pair[1].y, pair[1].x))
    for (_, previous), (_, current) in pairwise(ordered):
        assert previous is not None and current is not None
        gap = current.y - (previous.y + previous.height)
        if gap >= 0:
            gaps.append(gap)
    line_gap = _median(gaps) if gaps else 0.0

    return PageMetrics(
        left_margin=left_margin,
        measure_right=measure_right,
        character_width=character_width,
        line_gap=line_gap,
    )


def ends_sentence(text: str) -> bool:
    """True when ``text`` closes a sentence, ignoring trailing quotation marks."""

    stripped = text.rstrip()
    while stripped and stripped[-1] in _CLOSING_MARKS:
        stripped = stripped[:-1].rstrip()
    return bool(stripped) and stripped[-1] in _TERMINAL_PUNCTUATION


def _closing_only(text: str) -> bool:
    """True when a line carries nothing but closing marks and punctuation."""

    stripped = text.strip()
    return bool(stripped) and all(
        character in _CLOSING_MARKS or character in _TERMINAL_PUNCTUATION
        for character in stripped
    )


def _is_latin(character: str) -> bool:
    return character.isascii() and (character.isalnum())


def join_lines(left: str, right: str) -> str:
    """Join two visual lines the way a typesetter would have set them.

    Chinese text is joined with nothing: a paragraph is a continuous run of
    characters and the printed line break carries no information. A break that
    falls inside a Latin word or a number, though, needs a space — the typesetter
    moved the whole word down, so ``两条相碰撞的矢量线（vect`` + ``or）`` must read
    ``vector``, not ``vect or``; here the ``or`` belongs to the next word and the
    two are only separable by that space.
    """

    if not left:
        return right
    if not right:
        return left
    if _is_latin(left[-1]) and _is_latin(right[0]):
        return f"{left} {right}"
    return left + right


def _starts_new_paragraph(
    previous: TextLine,
    current: TextLine,
    geometry: PageMetrics | None,
    *,
    vertical: bool,
) -> bool:
    """Decide whether ``current`` begins a new paragraph after ``previous``."""

    if current.role is not previous.role:
        return True
    if current.role.is_standalone:
        # A title or a footnote is one unit; two consecutive ones are two units.
        return True
    if not previous.text.strip():
        return True

    if vertical:
        # A vertical column is a paragraph fragment: it ends a paragraph only when
        # the column ends a sentence. Indentation and measure do not apply.
        return ends_sentence(previous.text)

    if geometry is None:
        # Text-only fallback: punctuation is the only evidence left. Splitting at
        # every full stop is closer to the truth than one paragraph per line, and
        # it is the same rule the vertical splice uses.
        return ends_sentence(previous.text)

    assert previous.box is not None and current.box is not None
    indented = (current.box.x - geometry.left_margin) > geometry.indent_threshold
    previous_short = (geometry.measure_right - previous.right) > geometry.short_threshold
    gap = current.box.y - previous.bottom
    separated = gap > geometry.gap_threshold and geometry.line_gap > 0

    if separated:
        return True
    if indented and previous_short:
        # Both signals agree, which is the common case: a paragraph ended short and
        # the next one was indented.
        return True
    if ends_sentence(previous.text):
        # Punctuation alone is not enough: a full-measure line can end in a full
        # stop and continue on the next line. Demand corroboration from layout — the
        # next line indented, or the closed line short enough that the paragraph
        # cannot have filled the measure.
        return indented or previous_short
    # No punctuation, no indent: the sentence continues.
    return False


def assemble_paragraphs(
    lines: Sequence[TextLine], direction: PageDirection
) -> tuple[Paragraph, ...]:
    """Group reading-order lines into paragraphs with their roles."""

    kept: list[TextLine] = []
    for line in lines:
        if not line.text.strip():
            # A blank line is an explicit separator: keep it as one, with its role,
            # so the break survives into the export instead of being filtered away.
            kept.append(TextLine("", line.box, line.role))
            continue
        kept.append(line)
    if not any(line.text.strip() for line in kept):
        return ()

    geometry = measure_page([line for line in kept if line.text.strip()])
    vertical = direction.is_vertical
    paragraphs: list[Paragraph] = []
    role = LineRole.BODY
    text = ""
    previous: TextLine | None = None

    for current in kept:
        if not current.text.strip():
            if text:
                paragraphs.append(Paragraph(role, text))
            role, text, previous = LineRole.BODY, "", None
            continue
        if not text:
            role, text, previous = current.role, current.text.strip(), current
            continue
        if _closing_only(current.text):
            # A line of nothing but quotation marks belongs to the sentence it
            # closes, and it also ends that sentence: leaving the marks in their own
            # paragraph would orphan them, joining the next line onto them would
            # merge two paragraphs.
            paragraphs.append(Paragraph(role, join_lines(text, current.text.strip())))
            role, text, previous = LineRole.BODY, "", None
            continue
        assert previous is not None
        if _starts_new_paragraph(previous, current, geometry, vertical=vertical):
            paragraphs.append(Paragraph(role, text))
            role, text = current.role, current.text.strip()
        else:
            text = join_lines(text, current.text.strip())
        previous = current
    if text:
        paragraphs.append(Paragraph(role, text))
    return tuple(paragraphs)


#: Backwards-compatible private aliases: the names were module-private before the
#: flow layer needed them, and renaming callers of a private helper is churn without
#: benefit.
_ends_sentence = ends_sentence
_join = join_lines
_geometry = measure_page
