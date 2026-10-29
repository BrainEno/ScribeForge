from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from scribeforge.adapters.export_check import check_epub
from scribeforge.adapters.export_common import (
    IMAGES_DIR,
    XML_DECL,
    epub_image_name,
    format_modified,
    validate_book_text,
    validate_identifier,
    validate_language,
    write_atomic,
    write_zip_bytes,
    write_zip_entry,
)
from scribeforge.domain.content import VerifiedBook, VerifiedImage
from scribeforge.domain.flow import (
    AUTO_LAYOUT,
    FlowDocument,
    FlowParagraph,
    build_flow,
)
from scribeforge.domain.paragraphs import LineRole

_MIMETYPE = "application/epub+zip"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_EPUB_NS = "http://www.idpf.org/2007/ops"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_DC_NS = "http://purl.org/dc/elements/1.1/"
#: Where page rasters live inside the package. One directory keeps the manifest
#: and the integrity check's "every image is referenced once" rule simple.


def _container_xml() -> str:
    return (
        XML_DECL
        + "\n"
        + f'<container version="1.0" xmlns="{_CONTAINER_NS}">'
        + "<rootfiles>"
        + '<rootfile full-path="OEBPS/content.opf" '
        + 'media-type="application/oebps-package+xml"/>'
        + "</rootfiles></container>\n"
    )


def _package_xml(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: str,
    author: str | None,
    layout: str,
) -> str:
    items = [
        (
            '<item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>'
        )
    ]
    flow = build_flow(book, layout=layout)
    spine: list[str] = []
    for document in flow.documents:
        items.append(
            f'<item id="{document.item_id}" href="{document.file_name}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{document.item_id}"/>')
    cover = book.cover_image
    for image in book.images:
        properties = ' properties="cover-image"' if image is cover else ""
        items.append(
            f'<item id="{_image_item_id(image)}" href="{_image_href(image)}" '
            f'media-type="{image.media_type}"{properties}/>'
        )
    # EPUB 2 readers (and most library grids) only look at this meta name; EPUB 3
    # uses the manifest ``cover-image`` property, so both are written.
    cover_meta = (
        ""
        if cover is None
        else f'<meta name="cover" content="{_image_item_id(cover)}"/>'
    )
    creator = "" if author is None else f"<dc:creator>{escape(author)}</dc:creator>"
    return (
        XML_DECL
        + "\n"
        + f'<package xmlns="{_OPF_NS}" version="3.0" unique-identifier="book-id">'
        + f'<metadata xmlns:dc="{_DC_NS}">'
        + f'<dc:identifier id="book-id">{escape(identifier)}</dc:identifier>'
        + f"<dc:title>{escape(book.title)}</dc:title>"
        + creator
        + f"<dc:language>{escape(language)}</dc:language>"
        + f'<meta property="dcterms:modified">{modified}</meta>'
        + cover_meta
        + "</metadata>"
        + f"<manifest>{''.join(items)}</manifest>"
        + f"<spine>{''.join(spine)}</spine>"
        + "</package>\n"
    )


def _nav_entries(book: VerifiedBook, layout: str) -> str:
    """Nested table of contents built from the flow documents.

    Without a chapter tree a 562-page book exports as 562 flat entries called
    "Page 1".."Page 562", which is not a table of contents at all. When chapters
    exist the nav mirrors their levels; the per-page list is kept only as the
    fallback for a book whose structure has not been derived yet.
    """

    flow = build_flow(book, layout=layout)
    if flow.layout == "page":
        return "".join(
            f'<li><a href="{document.file_name}">'
            f"{escape(document.title)}</a></li>"
            for document in flow.documents
        )

    # Emit nested lists with an explicit stack of open levels. Building the
    # closing tags by string arithmetic on levels is easy to get wrong, and the
    # first attempt produced a mismatched-tag XHTML that the export integrity
    # check caught — which is exactly why that check exists.
    parts: list[str] = []
    stack: list[int] = []
    for document in flow.documents:
        link = (
            f'<li><a href="{document.file_name}">{escape(document.title)}</a>'
        )
        level = max(1, document.level)
        while stack and level < stack[-1]:
            parts.append("</li></ol>")
            stack.pop()
        if not stack:
            parts.append("<ol>")
            stack.append(level)
            parts.append(link)
        elif level == stack[-1]:
            parts.append("</li>")
            parts.append(link)
        else:
            parts.append("<ol>")
            stack.append(level)
            parts.append(link)
    while stack:
        parts.append("</li></ol>")
        stack.pop()
    return "".join(parts)


def _nav_xhtml(book: VerifiedBook, language: str, layout: str) -> str:
    lang = quoteattr(language)
    entries = _nav_entries(book, layout)
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{_XHTML_NS}" xmlns:epub="{_EPUB_NS}" lang={lang} xml:lang={lang}>'
        + "<head><title>Contents</title></head>"
        + f'<body><nav epub:type="toc" id="toc"><h1>Contents</h1>{entries}</nav></body>'
        + "</html>\n"
    )


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

    # ``quoteattr`` rather than ``escape``: the caption sits inside an attribute,
    # where a stray quotation mark would end the value and break the document.
    return (
        f'<figure class="page-image"><img src="{_image_href(image)}" '
        f"alt={quoteattr(image.alt)}/></figure>"
    )


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
    validate_book_text(book, author=author)
    modified_text = format_modified(modified)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        write_zip_entry(archive, "mimetype", _MIMETYPE, zipfile.ZIP_STORED)
        write_zip_entry(
            archive, "META-INF/container.xml", _container_xml(), zipfile.ZIP_DEFLATED
        )
        write_zip_entry(
            archive,
            "OEBPS/content.opf",
            _package_xml(
                book,
                identifier=identifier,
                language=language,
                modified=modified_text,
                author=author,
                layout=layout,
            ),
            zipfile.ZIP_DEFLATED,
        )
        write_zip_entry(
            archive,
            "OEBPS/nav.xhtml",
            _nav_xhtml(book, language, layout),
            zipfile.ZIP_DEFLATED,
        )
        for document in build_flow(book, layout=layout).documents:
            write_zip_entry(
                archive,
                f"OEBPS/{document.file_name}",
                _document_xhtml(document, language),
                zipfile.ZIP_DEFLATED,
            )
        # Page rasters are already compressed, so storing them keeps the archive
        # deterministic and avoids spending CPU to grow PNGs by a few bytes.
        for image in book.images:
            write_zip_bytes(
                archive,
                epub_image_name(image),
                image.data,
                zipfile.ZIP_STORED,
            )
    data = buffer.getvalue()
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
        ),
    )
