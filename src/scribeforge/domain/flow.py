"""Lay a verified book out as documents a reader reads, not as page images.

Everything upstream of this module is page-shaped: the scanner produced pages, the
engines read pages, and the decision layer certifies accepted text page by page.
That shape is correct for evidence and wrong for reading. A paragraph does not stop
because the typesetter ran out of paper, so a book exported "one XHTML file per
scanned page" arrives as a hundred paragraphs cut in half, each half in its own
file, none of them related to the other.

This module turns the page-shaped model into reading-shaped documents:

* a **chapter** is one document, so an EPUB reader's next-page gesture continues the
  chapter rather than jumping to a new file every time the scan turned a page;
* a paragraph that continues across a page turn is **one paragraph**, starting on
  the page where it began and ending on the page where it ended;
* the original pagination is not lost: each scanned page leaves a
  ``pagebreak`` marker carrying its printed folio, which is what citations need.

The merge decision is deliberately conservative and uses the same evidence as
paragraph reconstruction: a page's last line that stops short of the measure ends
its paragraph, and a next page whose first line is indented starts a new one. When
the geometry is unavailable the rule falls back to punctuation alone, exactly as
:mod:`scribeforge.domain.paragraphs` does.

A wrong merge costs a paragraph break; a wrong split costs the same. Neither loses
a character, and the export integrity check proves that per page.
"""

from __future__ import annotations

from dataclasses import dataclass

from scribeforge.domain.content import VerifiedBook, VerifiedPage
from scribeforge.domain.paragraphs import (
    LineRole,
    Paragraph,
    TextLine,
    ends_sentence,
    join_lines,
    measure_page,
)

#: Document file prefix per layout. Page layout keeps the historical names so an
#: existing store's exports stay comparable; chapter layout names by document.
PAGE_LAYOUT = "page"
CHAPTER_LAYOUT = "chapter"
#: ``auto`` = chapter documents when the book has a chapter tree, otherwise one
#: document per page. ``page`` forces a facsimile layout for comparing against the
#: scans; ``chapter`` insists on chapters and is only meaningful when they exist.
AUTO_LAYOUT = "auto"


@dataclass(frozen=True, slots=True)
class FlowParagraph:
    """One paragraph of the reading edition, with the pages it spans."""

    role: LineRole
    text: str
    start_page: int
    end_page: int
    printed_label: str = ""

    def __post_init__(self) -> None:
        if self.end_page < self.start_page:
            raise ValueError("a paragraph cannot end before it starts")
        if not self.text.strip():
            raise ValueError("a paragraph must carry text")


@dataclass(frozen=True, slots=True)
class FlowDocument:
    """One reading unit: a chapter, or a single page when there are no chapters."""

    index: int
    item_id: str
    file_name: str
    title: str
    pages: tuple[int, ...]
    paragraphs: tuple[FlowParagraph, ...]
    level: int = 1
    #: Every page carries a ``pagebreak`` marker, so a page with no text still
    #: leaves its number behind for a citation.
    page_labels: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BookFlow:
    """The whole book as ordered documents, plus which layout produced them."""

    documents: tuple[FlowDocument, ...]
    layout: str

    def __post_init__(self) -> None:
        if self.layout not in (PAGE_LAYOUT, CHAPTER_LAYOUT):
            raise ValueError(f"unknown layout: {self.layout}")
        indexes = [document.index for document in self.documents]
        if indexes != list(range(len(indexes))):
            raise ValueError("flow documents must be numbered from zero, in order")

    @property
    def paragraphs(self) -> tuple[FlowParagraph, ...]:
        return tuple(
            paragraph for document in self.documents for paragraph in document.paragraphs
        )


def _page_blocks(page: VerifiedPage) -> tuple[Paragraph, ...]:
    return page.blocks()


def _last_line(page: VerifiedPage) -> TextLine | None:
    lines = [line for line in page.text_lines() if line.text.strip()]
    return lines[-1] if lines else None


def _first_line(page: VerifiedPage) -> TextLine | None:
    lines = [line for line in page.text_lines() if line.text.strip()]
    return lines[0] if lines else None


def _previous_page_ended_the_paragraph(previous: VerifiedPage) -> bool:
    """True when the page's own evidence says its last paragraph is unfinished.

    Two independent facts, not one: the line did not close a sentence, and it ran
    to the end of the measure (a paragraph that ends at the foot of a page normally
    stops short). Punctuation alone is the fallback when the page has no usable
    geometry, which is the same rule paragraph reconstruction uses.
    """

    last = _last_line(previous)
    if last is None:
        return False
    if ends_sentence(last.text):
        return False
    metrics = measure_page(
        [line for line in previous.text_lines() if line.text.strip()]
    )
    if metrics is None or last.box is None:
        return True
    # A short final line means the paragraph ended even without punctuation.
    return not metrics.is_short(last)


def _next_page_continues_the_paragraph(next_page: VerifiedPage) -> bool:
    """True when the page's first line is set like a continuation, not a new start."""

    first = _first_line(next_page)
    if first is None:
        return False
    metrics = measure_page(
        [line for line in next_page.text_lines() if line.text.strip()]
    )
    if metrics is None or first.box is None:
        # No geometry: punctuation carries the decision, and the previous page's
        # rule already said the sentence was open.
        return True
    return not metrics.is_indented(first)


def _page_spans(book: VerifiedBook) -> dict[int, VerifiedPage]:
    return {page.page_index: page for page in book.pages}


def _group_pages(book: VerifiedBook) -> list[tuple[str, int, tuple[int, ...]]] | None:
    """Split the book into reading units: (title, level, page indexes).

    Returns ``None`` when there is no chapter structure to preserve, so the caller
    can fall back to one document per page — the historical behaviour, and honest:
    a chapter tree that does not match this book's pages must not silently become a
    different-looking book. With chapters, the units are the chapters plus whatever
    precedes the first, because front matter is a reading unit too; the last
    chapter's span runs to the end of the book.
    """

    pages = [page.page_index for page in book.pages]
    if not book.chapters or not pages:
        return None

    chapters = sorted(book.chapters, key=lambda chapter: chapter.page_index)
    available = set(pages)
    anchored = [chapter for chapter in chapters if chapter.page_index in available]
    if not anchored:
        return None

    grouped: list[tuple[str, int, tuple[int, ...]]] = []
    first = anchored[0].page_index
    before = tuple(page_index for page_index in pages if page_index < first)
    if before:
        grouped.append(("Front matter", 1, before))
    for position, chapter in enumerate(anchored):
        end = (
            anchored[position + 1].page_index
            if position + 1 < len(anchored)
            else max(pages) + 1
        )
        span = tuple(
            page_index
            for page_index in pages
            if chapter.page_index <= page_index < end
        )
        if span:
            grouped.append((chapter.title, chapter.level, span))
    # Anything after the last chapter is already inside it (its span runs to the
    # end of the book), so there is no separate back-matter document to build.
    return grouped


def _build_paragraphs(
    book: VerifiedBook, page_indexes: tuple[int, ...]
) -> tuple[FlowParagraph, ...]:
    """Collect a document's paragraphs, joining the ones a page turn interrupted."""

    spans = _page_spans(book)
    paragraphs: list[FlowParagraph] = []
    for page_index in page_indexes:
        page = spans[page_index]
        blocks = [block for block in _page_blocks(page) if block.text.strip()]
        if not blocks:
            continue
        for position, block in enumerate(blocks):
            open_previous = (
                paragraphs
                and paragraphs[-1].role is LineRole.BODY
                and paragraphs[-1].end_page == page_index - 1
                and block.role is LineRole.BODY
                and position == 0
                and _previous_page_ended_the_paragraph(spans[page_index - 1])
                and _next_page_continues_the_paragraph(page)
            )
            if open_previous:
                previous = paragraphs[-1]
                paragraphs[-1] = FlowParagraph(
                    role=previous.role,
                    text=join_lines(previous.text, block.text),
                    start_page=previous.start_page,
                    end_page=page_index,
                    printed_label=previous.printed_label,
                )
                continue
            paragraphs.append(
                FlowParagraph(
                    role=block.role,
                    text=block.text,
                    start_page=page_index,
                    end_page=page_index,
                    printed_label=page.printed_label,
                )
            )
    return tuple(paragraphs)


def build_flow(book: VerifiedBook, *, layout: str = AUTO_LAYOUT) -> BookFlow:
    """Turn a page-shaped verified book into reading-shaped documents.

    ``layout="page"`` reproduces the facsimile layout (one document per scanned
    page) that earlier versions always produced. It exists because a reviewer
    comparing an export against the scans wants that one-to-one correspondence, and
    because an integrity check should be able to verify either shape.
    """

    if layout not in (AUTO_LAYOUT, PAGE_LAYOUT, CHAPTER_LAYOUT):
        raise ValueError(f"unknown layout: {layout}")
    spans = _page_spans(book)
    grouped = _group_pages(book) if layout != PAGE_LAYOUT else None
    # Facsimile layout groups one scanned page per document no matter what the
    # chapter tree says: that shape exists precisely to compare against the scans.
    chapter_layout = grouped is not None
    groups = (
        grouped
        if grouped is not None
        else [("", 1, (page.page_index,)) for page in book.pages]
    )

    documents: list[FlowDocument] = []
    for index, (title, level, page_indexes) in enumerate(groups):
        if chapter_layout:
            item_id = f"doc-{index}"
            file_name = f"doc-{index + 1:06d}.xhtml"
            document_title = title or f"Pages {page_indexes[0] + 1}"
        else:
            page_index = page_indexes[0]
            item_id = f"page-{page_index}"
            file_name = f"page-{page_index + 1:06d}.xhtml"
            document_title = f"Page {spans[page_index].printed_label or page_index + 1}"
        documents.append(
            FlowDocument(
                index=index,
                item_id=item_id,
                file_name=file_name,
                title=document_title,
                pages=page_indexes,
                paragraphs=_build_paragraphs(book, page_indexes),
                level=level,
                page_labels=tuple(
                    (page_index, spans[page_index].printed_label)
                    for page_index in page_indexes
                ),
            )
        )
    return BookFlow(tuple(documents), CHAPTER_LAYOUT if chapter_layout else PAGE_LAYOUT)
