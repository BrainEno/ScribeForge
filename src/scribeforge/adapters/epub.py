"""Trusted EPUB export: accepted decisions only.

This module renders a verified book into content documents and delegates the
actual package assembly to the single shared builder in
:mod:`scribeforge.adapters.epub_builder` (the ``unverified=False`` mode), then
runs the book-level integrity check. The candidate exporter is the same
builder in its ``unverified=True`` downgrade mode, so the two layers cannot
drift apart (#72).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from scribeforge.adapters import epub_builder
from scribeforge.adapters.epub_builder import EpubDocument, EpubImage
from scribeforge.adapters.export_check import check_epub
from scribeforge.adapters.export_common import (
    IMAGES_DIR,
    XML_DECL,
    format_modified,
    validate_book_text,
    validate_identifier,
    validate_language,
    validate_normalization_policy,
    write_atomic,
)
from scribeforge.domain.content import VerifiedBook, VerifiedImage
from scribeforge.domain.flow import (
    AUTO_LAYOUT,
    FlowDocument,
    FlowParagraph,
    build_flow,
)
from scribeforge.domain.paragraphs import LineRole

_EPUB_NS = "http://www.idpf.org/2007/ops"
_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _image_item_id(image: VerifiedImage) -> str:
    return f"image-page-{image.page_index}"


def _image_href(image: VerifiedImage) -> str:
    """Where the image sits inside the package, as a manifest href."""

    return f"{IMAGES_DIR}/{image.file_name}"


def _figure_xhtml(image: VerifiedImage) -> str:
    """Render a scanned page that is a picture.

    ``alt`` is generated from recorded evidence only — the page's own printed
    number — rather than inventing a caption the book does not have. A plate with
    a printed caption under it is not read here; that is the OCR path's job, and
    this page has no OCR text by definition.
    """

    img = f'<img src="{_image_href(image)}" alt={quoteattr(image.alt)}/>'
    if image.caption:
        return (
            f'<figure class="page-image">{img}'
            f"<figcaption>{escape(image.caption)}</figcaption>"
            f"</figure>"
        )
    return f'<figure class="page-image">{img}</figure>'


def _block_xhtml(block: FlowParagraph) -> str:
    """Render one paragraph according to what it is.

    A scanned book's chapter title and its footnotes were previously just more
    body paragraphs, so a reader saw "Page 12" style prose with the chapter
    heading indistinguishable from text and every note dropped into the middle of
    the page. Headings and notes now carry their own markup, which is what makes
    a reading system able to style them and navigate them.
    """

    text = escape(block.text)
    if block.role is LineRole.HEADING:
        return f"<h2>{text}</h2>"
    if block.role is LineRole.FOOTNOTE:
        return f'<aside epub:type="footnote"><p>{text}</p></aside>'
    return f"<p>{text}</p>"


def _pagebreak_xhtml(page_index: int, printed_label: str) -> str:
    """Mark where a scanned page ends, so the book's own pagination survives.

    A reading edition must not lose the page numbers: a citation says "p. 137", and
    the printed folio is the only handle a reader has. The marker is invisible when
    the book is read straight through and is what a citation-aware reader jumps to.
    """

    label = printed_label or str(page_index + 1)
    return (
        f'<span epub:type="pagebreak" role="doc-pagebreak" '
        f'id="page-{page_index}" title="{escape(label)}"/>'
    )


def _document_xhtml(document: FlowDocument, language: str) -> str:
    lang = quoteattr(language)
    by_page: dict[int, list[FlowParagraph]] = {}
    for paragraph in document.paragraphs:
        by_page.setdefault(paragraph.start_page, []).append(paragraph)
    images_by_page: dict[int, list[VerifiedImage]] = {}
    for image in document.images:
        images_by_page.setdefault(image.page_index, []).append(image)
    parts: list[str] = []
    for page_index, printed_label in document.page_labels:
        parts.append(_pagebreak_xhtml(page_index, printed_label))
        parts.extend(_figure_xhtml(image) for image in images_by_page.get(page_index, ()))
        parts.extend(_block_xhtml(paragraph) for paragraph in by_page.get(page_index, ()))
    # A paragraph never starts outside a page of this document, so nothing can be
    # left over; assert it rather than silently dropping text.
    if sum(len(items) for items in by_page.values()) != len(document.paragraphs):
        raise ValueError("flow document has paragraphs that start outside its pages")
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{_XHTML_NS}" xmlns:epub="{_EPUB_NS}" lang={lang} xml:lang={lang}>'
        + f"<head><title>{escape(document.title)}</title></head>"
        + f"<body><section>{''.join(parts)}</section></body>"
        + "</html>\n"
    )


def build_epub(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: datetime,
    author: str | None = None,
    layout: str = AUTO_LAYOUT,
    normalization: str = "preserve",
) -> bytes:
    """Render a verified book as a deterministic EPUB 3 archive.

    ``layout`` decides whether chapters become documents (the default, which keeps a
    paragraph that spans a page turn in one piece) or every scanned page becomes its
    own document (a facsimile layout for checking against the scans).
    """

    if not book.pages:
        raise ValueError("cannot export a book with no pages")
    validate_identifier(identifier)
    validate_language(language)
    normalization = validate_normalization_policy(normalization)
    validate_book_text(book, author=author)

    flow = build_flow(book, layout=layout)
    documents = [
        EpubDocument(
            item_id=document.item_id,
            file_name=document.file_name,
            xhtml=_document_xhtml(document, language),
            nav_label=document.title,
            nav_level=max(1, document.level),
        )
        for document in flow.documents
    ]
    images = [
        EpubImage(
            item_id=_image_item_id(image),
            href=_image_href(image),
            media_type=image.media_type,
            data=image.data,
            cover=image is book.cover_image,
        )
        for image in book.images
    ]
    data = epub_builder.build_epub_package(
        title=book.title,
        identifier=identifier,
        language=language,
        modified=format_modified(modified),
        author=author,
        normalization=normalization,
        documents=documents,
        images=images,
        unverified=False,
    )
    check_epub(data, book, layout=layout)
    return data


def write_epub(
    book: VerifiedBook,
    path: Path,
    *,
    identifier: str,
    language: str,
    modified: datetime,
    author: str | None = None,
    layout: str = AUTO_LAYOUT,
    normalization: str = "preserve",
) -> Path:
    """Write an EPUB atomically so an interrupted export never leaves a torn file."""

    return write_atomic(
        path,
        build_epub(
            book,
            identifier=identifier,
            language=language,
            modified=modified,
            author=author,
            layout=layout,
            normalization=normalization,
        ),
    )
