from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath

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


class PageImageRole(str, Enum):
    """Why a scanned page is carried as a picture instead of reflowed text."""

    #: The book's cover: a reader expects it, and a store without one is what
    #: makes an EPUB look unfinished in a library grid.
    COVER = "cover"
    #: A plate, illustration or any other page whose content is an image.
    FIGURE = "figure"


#: Image formats an EPUB 3 reader is required to support and that the page
#: renderer actually produces. Anything else refuses to export rather than
#: embedding a file no reader can show.
IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass(frozen=True, slots=True)
class VerifiedImage:
    """A page whose content is a picture: the part of a book text cannot carry.

    Only a page with **no accepted text** becomes an image, and only when the book
    says it is a picture: the structure pass measured a cover or a plate, or a
    person confirmed the content is not text (issue #74). A page of prose is
    re-flowed, never mirrored: embedding its raster would ship an unsearchable
    picture where the project promised verified text.

    ``data`` is the raster captured while assembling the book, and ``sha256`` is
    its digest. The digest is re-derived on construction, so a model instance
    cannot claim bytes it does not carry; the export check re-derives it again
    from the archive entry that was actually written.
    """

    page_index: int
    file_name: str
    media_type: str
    role: PageImageRole
    sha256: str
    data: bytes = field(repr=False)
    alt: str = ""
    #: Optional caption read from the plate itself (one line of OCR text under
    #: the image). Rendered as ``<figcaption>`` in the EPUB; covers never have a
    #: caption.
    caption: str = ""

    def __post_init__(self) -> None:
        if self.media_type not in set(IMAGE_MEDIA_TYPES.values()):
            raise ValueError(f"unsupported image media type: {self.media_type!r}")
        if PurePosixPath(self.file_name).parent != PurePosixPath("."):
            raise ValueError("an embedded image file name must be a bare name")
        if not self.data:
            raise ValueError("an embedded image must carry bytes")
        actual = hashlib.sha256(self.data).hexdigest()
        if actual != self.sha256:
            raise ValueError(
                f"image digest {self.sha256[:12]}… does not match the bytes "
                f"captured for page {self.page_index}"
            )

    @property
    def is_cover(self) -> bool:
        return self.role is PageImageRole.COVER


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

    def text_lines(self) -> tuple[TextLine, ...]:
        """Accepted lines with the layout evidence that survived verification."""

        return tuple(
            TextLine(
                text,
                self.line_boxes[index] if self.line_boxes else None,
                self.line_roles[index] if self.line_roles else LineRole.BODY,
            )
            for index, text in enumerate(self.lines)
        )

    def blocks(self) -> tuple[Paragraph, ...]:
        """The page as paragraphs, with headings and footnotes kept distinct.

        ``lines`` stays the accepted evidence: one entry per aligned OCR line, in
        reading order, never rewritten. This method decides how those lines group
        into paragraphs for a reader, using the geometry and block labels the
        engines reported. Exporting raw lines would put a paragraph break at every
        printed line break, which is what a scanned book looks like, not what it
        reads like.
        """

        return assemble_paragraphs(self.text_lines(), self.direction)

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
    #: Pages that are pictures: the cover and any confirmed plate. Ordered by page.
    images: tuple[VerifiedImage, ...] = ()

    def __post_init__(self) -> None:
        pages = {page.page_index for page in self.pages}
        seen: set[int] = set()
        for image in self.images:
            if image.page_index not in pages:
                raise ValueError(
                    f"image for page {image.page_index} is not part of the book"
                )
            if image.page_index in seen:
                raise ValueError(f"page {image.page_index} carries two images")
            seen.add(image.page_index)
        for page in self.pages:
            if page.lines and page.page_index in seen:
                matched: VerifiedImage | None = next(
                    (
                        img
                        for img in self.images
                        if img.page_index == page.page_index
                    ),
                    None,
                )
                # A plate may carry one line of OCR text as its caption; any
                # other page must stay text-only if it ships as an image.
                if (
                    matched is None
                    or matched.role is not PageImageRole.FIGURE
                    or len(page.lines) != 1
                    or page.lines[0] != matched.caption
                ):
                    raise ValueError(
                        f"page {page.page_index} has both accepted text and an image"
                    )

    @property
    def cover_image(self) -> VerifiedImage | None:
        """The image an EPUB should declare as the book cover, if there is one."""

        return self.images[0] if self.images and self.images[0].is_cover else None
