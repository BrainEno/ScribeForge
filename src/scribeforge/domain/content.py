from __future__ import annotations

from dataclasses import dataclass, field

from scribeforge.domain.acceptance import AcceptanceSummary
from scribeforge.domain.ocr import BoundingBox, PageDirection
from scribeforge.domain.paragraphs import (
    LineRole,
    Paragraph,
    TextLine,
    assemble_paragraphs,
)


@dataclass(frozen=True, slots=True)
class DroppedFurniture:
    """A running head, footer or folio excluded from the exported text.

    Carried on the book rather than discarded so an export can report exactly what
    it removed: silently dropping text is the failure mode furniture detection has
    to avoid.
    """

    page_index: int
    text: str
    reason: str


@dataclass(frozen=True, slots=True)
class VerifiedPage:
    """One verified page: its source index and accepted lines in reading order.

    ``printed_label`` is the page number printed in the book, which is *not* the
    physical index: this book restarts numbering per section and leaves some pages
    unnumbered. Export shows the printed label to readers and keeps the physical
    index for navigation and evidence references.
    """

    page_index: int
    lines: tuple[str, ...]
    printed_label: str = ""
    direction: PageDirection = PageDirection.HORIZONTAL_LR
    #: Layout evidence for each accepted line, in reading order. Optional so a
    #: caller that only has text (tests, older stores) still gets sane output.
    #: Layout evidence is deliberately excluded from equality: a page *is* its
    #: accepted text and direction. Two runs that agree on the text are the same
    #: page even if one engine gave tighter boxes.
    line_boxes: tuple[BoundingBox | None, ...] = field(default=(), compare=False)
    line_roles: tuple[LineRole, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if self.line_boxes and len(self.line_boxes) != len(self.lines):
            raise ValueError("line boxes must match the accepted lines one to one")
        if self.line_roles and len(self.line_roles) != len(self.lines):
            raise ValueError("line roles must match the accepted lines one to one")

    def blocks(self) -> tuple[Paragraph, ...]:
        """The page as paragraphs, with headings and footnotes kept distinct.

        ``lines`` stays the accepted evidence: one entry per aligned OCR line, in
        reading order, never rewritten. This method decides how those lines group
        into paragraphs for a reader, using the geometry and block labels the
        engines reported. Exporting raw lines would put a paragraph break at every
        printed line break, which is what a scanned book looks like, not what it
        reads like.
        """

        text_lines = [
            TextLine(
                text,
                self.line_boxes[index] if self.line_boxes else None,
                self.line_roles[index] if self.line_roles else LineRole.BODY,
            )
            for index, text in enumerate(self.lines)
        ]
        return assemble_paragraphs(text_lines, self.direction)

    def paragraphs(self) -> tuple[str, ...]:
        """Paragraph text only, for callers that do not care about roles."""

        return tuple(paragraph.text for paragraph in self.blocks())


@dataclass(frozen=True, slots=True)
class VerifiedChapter:
    """A chapter as it appears to a reader, anchored to a physical page."""

    title: str
    level: int
    page_index: int
    printed_page: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedBook:
    """The accepted text of a project, assembled only from recorded decisions.

    This model never carries OCR candidate text: every line is the newest
    accepted decision for its pair, so export cannot silently fall back to raw
    engine output.
    """

    title: str
    pages: tuple[VerifiedPage, ...]
    chapters: tuple[VerifiedChapter, ...] = ()
    dropped_furniture: tuple[DroppedFurniture, ...] = ()
    #: Pages that carry no text and were confirmed to be textless (a blank leaf or
    #: a plate). Nothing enters this list by inference: both engines returning
    #: nothing is also what a crash looks like (issue #74).
    confirmed_blank_pages: tuple[int, ...] = ()
    #: Pairs where both engines returned an empty line. Recorded rather than
    #: silently dropped, so a reviewer can see how much of a page was empty.
    empty_pairs: tuple[tuple[int, int], ...] = ()
    #: Who accepted the exported text and how risky the machine-accepted part was.
    acceptance: AcceptanceSummary = field(default_factory=AcceptanceSummary)
